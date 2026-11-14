"""Английская версия сайта: словарь фраз и подключение переводчика на страницы."""
import glob
import json
import os
import re
import socket
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
CYR = re.compile(r"[А-Яа-яЁё]")


def test_dictionary_is_english_and_built():
    words = json.loads((ROOT / "web/i18n/en.json").read_text(encoding="utf-8"))
    assert len(words) > 900
    bad = [k for k, v in words.items() if not isinstance(v, str) or not v.strip() or CYR.search(v)]
    assert not bad, bad[:5]
    built = (ROOT / "web/static/i18n_en.js").read_text(encoding="utf-8")
    body = json.loads(built[built.index("{"):built.rindex("}") + 1])
    assert body == words, "web/static/i18n_en.js устарел -- запустите scripts/i18n_build.py"


def test_every_page_loads_the_translator_once(monkeypatch, tmp_path):
    monkeypatch.setenv("MIDI2TAB_DATA", str(tmp_path / "data"))
    import sys

    for name in [m for m in sys.modules if m.startswith("web.")]:
        del sys.modules[name]
    from fastapi.testclient import TestClient

    import web.app as app_module

    with TestClient(app_module.app) as client:
        for path in ("/", "/studio", "/pricing", "/account", "/offer", "/privacy", "/podbor-akkordov"):
            html = client.get(path).text
            assert html.count("/static/i18n.js?v=") == 1, path
            # первым скриптом в <head>, сразу после кодировки -- до того, как страница видна
            head = html[:html.index("</head>")]
            assert head.index("i18n.js") < head.index("<title>") if "<title>" in head else True, path


def test_every_release_note_has_an_english_translation():
    """Окно «Что нового» и /updates -- и по-английски: у каждой фразы
    обновления должен быть перевод, иначе новая версия выйдет полурусской."""
    import json as _json
    import sys as _sys
    from pathlib import Path as _Path

    root = _Path(__file__).resolve().parent.parent
    _sys.path.insert(0, str(root))
    from web import releases

    en = _json.loads((root / "web" / "i18n" / "en.json").read_text(encoding="utf-8"))
    missing = []
    for release in releases.RELEASES:
        phrases = [release.title, release.summary, f"Что нового: {release.title.lower()}"]
        for change in release.changes:
            phrases += [change.title, change.text, change.kind_label] + ([change.where] if change.where else [])
        missing += [p for p in phrases if p not in en]
    assert not missing, f"нет перевода в web/i18n/en.json: {missing}"


def _chromium_executable(pw):
    """Та же находка, что в test_mixer_browser.py: версия браузера,
    поставленная playwright.install(), может не совпасть с тем, что уже
    лежит в образе -- ищем любой chrome, какой найдётся."""
    try:
        browser = pw.chromium.launch(args=["--no-sandbox"])
        browser.close()
        return None
    except Exception:
        pass
    for pattern in (
        os.path.join(os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "/opt/pw-browsers"),
                     "chromium-*", "chrome-linux*", "chrome"),
        os.path.expanduser("~/.cache/ms-playwright/chromium-*/chrome-linux*/chrome"),
    ):
        found = sorted(glob.glob(pattern))
        if found:
            return found[-1]
    return None


def test_credit_pack_paragraph_is_fully_translated_in_english(monkeypatch, tmp_path):
    """Регресс 14.11: «Докупить кредиты» получила выбор количества, а
    пояснение под ним («1 кредит ≈ … ₽. … Кредиты не сгорают…») по-английски
    осталось частично русским -- в словаре не было ни «1 кредит ≈ », ни
    самого предложения про несгорающие кредиты, и переводчик (web/static/i18n.js)
    их просто не находил. Проверяем живым браузером: ничего кириллического
    не должно остаться внутри #credits на /pricing при lang=en."""
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    monkeypatch.setenv("MIDI2TAB_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("MIDI2TAB_SECRET", "test-secret")
    for name in [m for m in sys.modules if m.startswith("web.")]:
        del sys.modules[name]
    uvicorn = pytest.importorskip("uvicorn")
    import web.app as app_module

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    config = uvicorn.Config(app_module.app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(200):
        if server.started:
            break
        time.sleep(0.05)
    else:
        server.should_exit = True
        pytest.fail("сервер не поднялся за 10 секунд")
    base_url = f"http://127.0.0.1:{port}"
    try:
        with sync_playwright() as pw:
            executable = _chromium_executable(pw)
            try:
                browser = pw.chromium.launch(executable_path=executable, args=["--no-sandbox"])
            except Exception as error:
                pytest.skip(f"браузер playwright не поднялся: {error}")
            try:
                context = browser.new_context()
                context.add_cookies([{"name": "lang", "value": "en", "url": base_url}])
                page = context.new_page()
                page.goto(f"{base_url}/pricing", wait_until="networkidle")
                text = page.inner_text("#credits")
                assert not CYR.search(text), f"остался русский текст в #credits: {text!r}"
            finally:
                browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)
