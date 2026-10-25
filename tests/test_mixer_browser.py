"""Мультитрек на телефоне (web/static/mix.js) живым браузером.

Вся логика пульта дорожки (перетаскивание, M/S, свайп регуляторов) живёт
в JS, pytest её не видит -- находка 10-23 (клик мышью по M/S в шапке
выбирал дорожку вместо своего действия) нашлась только благодаря разовому
запуску Chromium+Playwright, написанному вручную для того прогона и не
сохранённому в репозитории. Этот файл -- постоянная версия той проверки
для самого денежного пути пульта (M/S/соло), как предлагалось в находках
10-23 и 10-24. Playwright здесь нет в requirements -- тест сам пропускает
себя, если библиотеки или браузера нет (как и test_beatnet.py с тяжёлыми
моделями).
"""

from __future__ import annotations

import glob
import os
import socket
import subprocess
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
    """Браузер playwright.install() может не совпасть версией с тем, что
    уже лежит в образе (PLAYWRIGHT_BROWSERS_PATH) -- ищем любой chrome,
    какой найдётся, вместо жёсткой привязки к одному пути."""
    try:
        browser = pw.chromium.launch(args=["--no-sandbox"])
        browser.close()
        return None     # путь по умолчанию работает -- отдельный не нужен
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
def live_mixer(tmp_path, monkeypatch):
    monkeypatch.setenv("MIDI2TAB_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("MIDI2TAB_SECRET", "test-secret")
    monkeypatch.setenv("RUNPOD_API_KEY", "rp-test")
    monkeypatch.setenv("NASLUX_WORKER_ENDPOINT", "ep1")
    monkeypatch.setenv("NASLUX_COVERS", "0")
    for name in [m for m in sys.modules if m.startswith("web.")]:
        del sys.modules[name]
    uvicorn = pytest.importorskip("uvicorn")
    import web.app as app_module
    from web import billing

    user = app_module.storage.ensure_user(None)
    billing.grant_subscription(app_module.storage, user.id, days=1, tier="studio_month")

    job = app_module.storage.create_job(user.id, "song.mp3", {"kind": "studio", "mode": "stems",
                                                               "title": "Разделение на партии"})
    folder = app_module.studio_runner.folder(job.id)
    os.makedirs(folder, exist_ok=True)
    for name, freq in (("vocals", 440), ("drums", 90)):
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
                        "-i", f"sine=frequency={freq}:duration=1", f"{folder}/{name}.mp3"], check=True)
    app_module.storage.update_job(job.id, status="done", result={"files": [
        {"name": "vocals.mp3", "label": "Вокал"}, {"name": "drums.mp3", "label": "Барабаны"}]})

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
        yield app_module, user, job, port
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def test_mouse_click_on_mute_or_solo_toggles_the_button_not_the_track_selection(live_mixer):
    """Регресс находки 10-23: мышь (в отличие от пальца) захватывает
    указатель на pointerdown без задержки -- без guard-условия на
    <button> клик по M/S доставался не кнопке, а всей шапке дорожки
    (data-grab), и дорожка просто выделялась."""
    app_module, user, job, port = live_mixer
    cookie_value = app_module.signer.dumps(user.id)
    base_url = f"http://127.0.0.1:{port}"

    with sync_playwright() as pw:
        executable = _chromium_executable(pw)
        try:
            browser = pw.chromium.launch(executable_path=executable, args=["--no-sandbox"])
        except Exception as error:
            pytest.skip(f"браузер playwright не поднялся: {error}")
        try:
            context = browser.new_context(viewport={"width": 390, "height": 760})
            context.add_cookies([{"name": "uid", "value": cookie_value, "url": base_url}])
            page = context.new_page()
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(f"{base_url}/studio/mix/{job.id}", wait_until="networkidle")

            mute = page.locator('.mx-headms[data-mute="0"]')
            solo = page.locator('.mx-headms[data-solo="0"]')
            row = page.locator('.mx-row[data-i="0"]')
            mute.wait_for(state="visible", timeout=8000)

            mute.click()
            assert mute.get_attribute("aria-pressed") == "true"
            assert "picked" not in (row.get_attribute("class") or "")

            solo.click()
            assert solo.get_attribute("aria-pressed") == "true"
            assert "picked" not in (row.get_attribute("class") or "")

            assert errors == []
        finally:
            browser.close()
