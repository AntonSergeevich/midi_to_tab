"""Студия (web/static/studio.js) живым браузером.

13.11: на новом сервере нет ключа Mureka, переделку своей записи и песни
с нуля стал вести Suno через GenAPI (web/studio.py::suno_public). Логика
показа «тонких настроек» (strengthBox -- «сила исходника»/«удержание
мелодии») в showMode() проверяла только «не Mureka и не YuE2», считая
оставшийся случай ACE-Step/RunPod -- но теперь оставшийся случай часто
Suno, у которого этих ручек вообще нет (backend молча игнорирует
audio_influence/melody, когда engine == "suno", см. api_studio_start).
Playwright здесь нет в requirements -- тест сам пропускает себя, если
библиотеки или браузера нет (как test_mixer_browser.py).
"""

from __future__ import annotations

import glob
import os
import socket
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _chromium_executable(pw):
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


@pytest.fixture
def live_studio_suno_only(tmp_path, monkeypatch):
    """Студия без ключа Mureka -- как на новом сервере 13.11: переделку и
    песни с нуля ведёт Suno (GenAPI), RunPod/ACE-Step формально подключён
    (нужен для studio.available()), но до него дело не доходит."""
    monkeypatch.setenv("MIDI2TAB_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("MIDI2TAB_SECRET", "test-secret")
    monkeypatch.setenv("RUNPOD_API_KEY", "rp-test")
    monkeypatch.setenv("NASLUX_WORKER_ENDPOINT", "ep1")
    monkeypatch.setenv("NASLUX_COVERS", "0")
    monkeypatch.delenv("MUREKA_API_KEY", raising=False)
    monkeypatch.setenv("NASLUX_YUE2", "0")
    monkeypatch.setenv("GENAPI_API_KEY", "ga-test")
    for name in [m for m in sys.modules if m.startswith("web.")]:
        del sys.modules[name]
    uvicorn = pytest.importorskip("uvicorn")
    import web.app as app_module

    user = app_module.storage.ensure_user(None)

    port = _free_port()
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
    try:
        yield app_module, user, port
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def test_restyle_on_suno_hides_the_ace_step_strength_sliders(live_studio_suno_only):
    """Регресс 13.11: без ключа Mureka переделка своей записи идёт через
    Suno, у которого нет «силы исходника»/«удержания мелодии» (это ручки
    ACE-Step/RunPod) -- showMode() должен прятать strengthBox и в этом
    случае, не только при Mureka/YuE2."""
    app_module, user, port = live_studio_suno_only
    base_url = f"http://127.0.0.1:{port}"

    with sync_playwright() as pw:
        executable = _chromium_executable(pw)
        try:
            browser = pw.chromium.launch(executable_path=executable, args=["--no-sandbox"])
        except Exception as error:
            pytest.skip(f"браузер playwright не поднялся: {error}")
        try:
            context = browser.new_context(viewport={"width": 1300, "height": 900})
            context.add_cookies([{"name": "uid", "value": app_module.signer.dumps(user.id), "url": base_url}])
            page = context.new_page()
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(f"{base_url}/studio", wait_until="networkidle")
            page.locator('[data-mode="restyle"]').click()
            # hitOpts и strengthBox живут внутри сворачиваемых «Тонких настроек»
            # (видимость там решает отдельный CSS-переключатель) -- проверяем
            # именно HTML-атрибут hidden, который выставляет showMode(), а не
            # фактическую видимость на экране.
            page.wait_for_function(
                "document.getElementById('hitOpts').hasAttribute('hidden') === false", timeout=5000)
            assert page.locator("#strengthBox").get_attribute("hidden") is not None, (
                "ползунки ACE-Step («сила исходника»/«удержание мелодии») не "
                "скрыты при переделке на Suno, хотя backend их там полностью игнорирует")
            assert errors == []
        finally:
            browser.close()
