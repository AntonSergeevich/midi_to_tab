"""Вход через Яндекс ID и VK ID.

С 1 декабря 2023 года (149-ФЗ, ст. 8 ч. 10; штраф -- КоАП 13.55) российский
сайт авторизует пользователей только по номеру телефона, через Госуслуги,
биометрию или российские сервисы входа. Яндекс ID и VK ID -- как раз такие;
Google, Apple, Telegram и другие иностранные -- нельзя.

Ключи приложений -- в окружении сервера:
  YANDEX_CLIENT_ID / YANDEX_CLIENT_SECRET  -- oauth.yandex.ru, «Веб-сервисы»,
      Redirect URI https://naslux.ru/api/auth/yandex/callback, доступ к почте;
  VK_CLIENT_ID                             -- id.vk.ru (кабинет VK ID), «Веб-сайт», доверенный
      Redirect URL https://naslux.ru/api/auth/vk/callback, доступ к почте.
Без ключей кнопки входа просто не показываются.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import urllib.error
import urllib.parse
import urllib.request

USER_AGENT = "naslux-auth/1.0"


def vk_host() -> str:
    """Адрес VK ID: теперь id.vk.ru (раньше id.vk.com). Переадресацию POST
    обмена кода urllib не повторяет, поэтому ходим сразу на новый адрес;
    если VK снова переедет -- VK_ID_HOST в окружении."""
    return os.environ.get("VK_ID_HOST", "id.vk.ru")


def configured() -> dict[str, bool]:
    return {"yandex": bool(os.environ.get("YANDEX_CLIENT_ID") and os.environ.get("YANDEX_CLIENT_SECRET")),
            "vk": bool(os.environ.get("VK_CLIENT_ID"))}


def _open(request) -> dict:
    """Ответ сервиса как JSON; ошибку сервиса -- его же словами, а не «HTTP Error 400»."""
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as error:
        raw = error.read()[:2000]
        try:
            answer = json.loads(raw or b"{}")
        except ValueError:
            answer = {}
        detail = answer.get("error_description") or answer.get("error") or raw.decode(errors="replace")[:200]
        raise ValueError(f"{error.code}: {detail}") from None


def _post(url: str, fields: dict) -> dict:
    return _open(urllib.request.Request(url, data=urllib.parse.urlencode(fields).encode(), method="POST",
                                        headers={"User-Agent": USER_AGENT,
                                                 "Content-Type": "application/x-www-form-urlencoded"}))


def _get(url: str, headers: dict) -> dict:
    return _open(urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **headers}))


def start(provider: str, redirect_uri: str) -> tuple[str, dict]:
    """Адрес страницы входа у сервиса и то, что нужно запомнить до возврата
    (state против подделки запроса, code_verifier для PKCE у VK)."""
    state = secrets.token_urlsafe(24)
    if provider == "yandex":
        query = {"response_type": "code", "client_id": os.environ["YANDEX_CLIENT_ID"],
                 "redirect_uri": redirect_uri, "state": state}
        return "https://oauth.yandex.ru/authorize?" + urllib.parse.urlencode(query), {"state": state}
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    query = {"response_type": "code", "client_id": os.environ["VK_CLIENT_ID"],
             "redirect_uri": redirect_uri, "state": state, "scope": "email",
             "code_challenge": challenge, "code_challenge_method": "S256"}
    return f"https://{vk_host()}/authorize?" + urllib.parse.urlencode(query), \
        {"state": state, "verifier": verifier}


def finish(provider: str, params: dict, remembered: dict, redirect_uri: str) -> dict:
    """Код от сервиса -> {"id", "email", "name"}. Бросает ValueError, если что-то не так."""
    if not remembered or params.get("state") != remembered.get("state"):
        raise ValueError("Вход устарел или подделан — попробуйте ещё раз")
    code = params.get("code")
    if not code:
        raise ValueError(params.get("error_description") or params.get("error") or "Вход отменён")
    if provider == "yandex":
        token = _post("https://oauth.yandex.ru/token", {
            "grant_type": "authorization_code", "code": code,
            "client_id": os.environ["YANDEX_CLIENT_ID"],
            "client_secret": os.environ["YANDEX_CLIENT_SECRET"]})
        if not token.get("access_token"):
            raise ValueError("Яндекс не выдал доступ")
        info = _get("https://login.yandex.ru/info?format=json",
                    {"Authorization": f"OAuth {token['access_token']}"})
        return {"id": str(info["id"]), "email": (info.get("default_email") or "").lower(),
                "name": info.get("real_name") or info.get("display_name") or ""}
    token = _post(f"https://{vk_host()}/oauth2/auth", {
        "grant_type": "authorization_code", "code": code, "code_verifier": remembered["verifier"],
        "client_id": os.environ["VK_CLIENT_ID"], "device_id": params.get("device_id", ""),
        "redirect_uri": redirect_uri, "state": params.get("state", "")})
    if not token.get("access_token"):
        raise ValueError("VK не выдал доступ: " + str(token.get("error_description") or token.get("error") or token)[:200])
    info = _post(f"https://{vk_host()}/oauth2/user_info", {
        "access_token": token["access_token"], "client_id": os.environ["VK_CLIENT_ID"]}).get("user") or {}
    if not info.get("user_id"):
        raise ValueError("VK не вернул пользователя")
    return {"id": str(info["user_id"]), "email": (info.get("email") or "").lower(),
            "name": " ".join(filter(None, [info.get("first_name"), info.get("last_name")]))}
