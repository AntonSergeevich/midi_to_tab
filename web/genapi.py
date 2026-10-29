"""
GenAPI (gen-api.ru): Suno и другие музыкальные нейросети по одному ключу,
оплата в рублях. Документация: https://gen-api.ru/model/suno/api

Запрос -- POST /networks/<модель> (ответ: номер запроса), результат --
GET /request/get/<номер>: status processing / success / error, в result --
ссылки на файлы (живут сутки, поэтому сразу скачиваем к себе).

Ключ -- как у ИИ-шлюза: из настроек сервера (GENAPI_API_KEY), а если там
его нет -- из файла в папке данных, который присылает проверка сайта из
секрета GitHub (llm-key-sync.yml).
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

API = "https://api.gen-api.ru/api/v1"
KEY_FILE_NAME = "genapi_key"
SUNO = "suno"
SUNO_MODEL = "v6"
STATES = {"processing": "Пишем песню", "starting": "В очереди", "queued": "В очереди"}


class GenApiNoMoney(RuntimeError):
    """На балансе GenAPI кончились деньги."""


def _key_file() -> str:
    return os.path.join(os.environ.get("MIDI2TAB_DATA", "data"), KEY_FILE_NAME)


def key() -> str:
    env = os.environ.get("GENAPI_API_KEY", "").strip().strip("\"'").strip()
    if len(env) >= 20:
        return env
    try:
        with open(_key_file(), encoding="utf-8") as f:
            return f.read().strip() or env
    except OSError:
        return env


def save_key(value: str) -> None:
    value = value.strip()
    if not (20 <= len(value) <= 300) or not value.isascii() or any(ch.isspace() for ch in value):
        raise ValueError("ключ не похож на ключ GenAPI")
    path = _key_file()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w", encoding="utf-8") as f:
        f.write(value)
    os.replace(tmp, path)


def ready() -> bool:
    return bool(key()) and os.environ.get("NASLUX_GENAPI", "1") == "1"


def call(method: str, path: str, body: dict | None = None, timeout: int = 60) -> dict:
    """Запрос к GenAPI. Ошибки -- RuntimeError с коротким текстом (без ключа)."""
    request = urllib.request.Request(
        f"{API}{path}", method=method,
        data=json.dumps(body, ensure_ascii=False).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {key()}", "Content-Type": "application/json",
                 "Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", "replace")[:300]
        try:
            detail = str(json.loads(detail).get("error") or json.loads(detail).get("message") or detail)
        except (ValueError, AttributeError):
            pass
        if error.code == 402 or "баланс" in detail.lower() or "недостаточно" in detail.lower():
            raise GenApiNoMoney(f"GenAPI: {detail}") from error
        raise RuntimeError(f"GenAPI ответил {error.code}: {detail}") from error
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise RuntimeError(f"GenAPI недоступен: {error}") from error


def request_id(started: dict) -> str:
    """Номер запроса из ответа на POST (поле называется по-разному)."""
    for name in ("request_id", "id"):
        if started.get(name) not in (None, ""):
            return str(started[name])
    raise RuntimeError(f"GenAPI не дал номер запроса: {json.dumps(started, ensure_ascii=False)[:200]}")


def start(network: str, params: dict) -> str:
    return request_id(call("POST", f"/networks/{network}", params))


def status(rid: str) -> dict:
    return call("GET", f"/request/get/{rid}")


def wait(rid: str, timeout: int = 900, every: int = 5, progress=None) -> dict:
    """Ждать конца запроса; progress(stage, seconds) -- на каждом опросе."""
    started = time.time()
    while True:
        state = status(rid)
        kind = str(state.get("status") or "")
        if kind == "success":
            return state
        if kind in ("error", "failed", "cancelled"):
            raise RuntimeError(f"GenAPI: {error_text(state)}")
        elapsed = time.time() - started
        if elapsed > timeout:
            raise RuntimeError("GenAPI не справился за отведённое время")
        if progress:
            progress(STATES.get(kind, "Пишем песню"), elapsed)
        time.sleep(every)


def error_text(state: dict) -> str:
    for name in ("error", "message", "detail"):
        if state.get(name):
            return str(state[name])[:300]
    full = state.get("full_response") or []
    if full and isinstance(full[0], dict):
        for name in ("error", "message", "errorMessage"):
            if full[0].get(name):
                return str(full[0][name])[:300]
    return str(state.get("status") or "ошибка")


def tracks(state: dict) -> list[dict]:
    """Готовые треки: ссылка на звук, длина, id у Suno (для продления и
    дорожек), название и стиль. Ссылки ищем и в result, и в full_response
    -- ответ провайдеров бывает разной формы."""
    found: list[dict] = []
    seen: set[str] = set()

    def add(url: str, extra: dict | None = None) -> None:
        if isinstance(url, str) and url.startswith("http") and url not in seen:
            seen.add(url)
            extra = extra or {}
            found.append({"url": url, "id": str(extra.get("id") or extra.get("audio_id") or ""),
                          "seconds": extra.get("duration") or 0, "title": extra.get("title") or "",
                          "tags": extra.get("tags") or "", "image": extra.get("image_url") or ""})

    for item in state.get("full_response") or []:
        if not isinstance(item, dict):
            continue
        for sub in item.get("data") if isinstance(item.get("data"), list) else [item]:
            if isinstance(sub, dict):
                add(sub.get("audio_url") or sub.get("stream_audio_url") or sub.get("url") or "", sub)
    for url in state.get("result") or []:
        if isinstance(url, str) and not url.lower().split("?")[0].endswith((".jpg", ".jpeg", ".png", ".webp")):
            add(url)
    return found


def download(url: str, path: str, timeout: int = 180) -> int:
    request = urllib.request.Request(url, headers={"User-Agent": "naslux/1.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response, open(path + ".part", "wb") as out:
        size = 0
        while chunk := response.read(1 << 20):
            size += len(chunk)
            out.write(chunk)
    os.replace(path + ".part", path)
    return size


def suno_params(task: str, *, title: str = "", style: str = "", lyrics: str = "", instrumental: bool = False,
                gender: str = "", negative: str = "", style_weight: float | None = None,
                weirdness: float | None = None, audio_weight: float | None = None,
                upload_url: str = "", audio_id: str = "", continue_at: float | None = None,
                model: str = "", callback_url: str = "") -> dict:
    """Параметры Suno на GenAPI. Текст -- на русском как есть: перевод
    (translate_input) по умолчанию включён и перевёл бы песню на английский."""
    params: dict = {"model": model or os.environ.get("NASLUX_SUNO_MODEL", SUNO_MODEL), "task": task,
                    "translate_input": False, "custom_mode": True, "instrumental": bool(instrumental),
                    "title": (title or "NASLUX")[:100], "tags": style[:1000]}
    if lyrics and not instrumental:
        params["prompt"] = lyrics[:5000]
    if gender in ("m", "f"):
        params["vocal_gender"] = gender
    if negative:
        params["negative_tags"] = negative[:200]
    for name, value in (("style_weight", style_weight), ("weirdness_constraint", weirdness),
                        ("audio_weight", audio_weight)):
        if value is not None:
            params[name] = round(min(max(float(value), 0.0), 1.0), 2)
    if upload_url:
        params["upload_url"] = upload_url
    if audio_id:
        params["audio_id"] = audio_id
        params["default_param_flag"] = True
    if continue_at:
        params["continue_at"] = round(float(continue_at), 1)
    if callback_url:
        params["callback_url"] = callback_url
    return params
