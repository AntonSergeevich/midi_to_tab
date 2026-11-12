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


def test_dragging_a_clip_edge_with_the_mouse_trims_the_track(live_mixer):
    """12.11, просьба владельца: потянул за край дорожки -- она обрезалась.
    Над краем курсор «⟷», левый край ловится и у дорожки, что начинается
    с 0:00 (прижат к краю окна), правый -- так же."""
    app_module, user, job, port = live_mixer
    base_url = f"http://127.0.0.1:{port}"
    with sync_playwright() as pw:
        executable = _chromium_executable(pw)
        try:
            browser = pw.chromium.launch(executable_path=executable, args=["--no-sandbox"])
        except Exception as error:
            pytest.skip(f"браузер playwright не поднялся: {error}")
        try:
            context = browser.new_context(viewport={"width": 1300, "height": 800})
            context.add_cookies([{"name": "uid", "value": app_module.signer.dumps(user.id), "url": base_url}])
            page = context.new_page()
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(f"{base_url}/studio/mix/{job.id}", wait_until="networkidle")
            wave = page.locator('[data-wave="0"]')
            wave.wait_for(state="visible", timeout=8000)
            page.wait_for_function("typeof tracks !== 'undefined' && tracks[0] && tracks[0].clips.length > 0")
            box = wave.bounding_box()
            y = box["y"] + box["height"] / 2

            page.mouse.move(box["x"] + 5, y)                       # у самого левого края, внутри куска
            assert wave.evaluate("el => el.style.cursor") == "ew-resize"
            page.mouse.down()
            page.mouse.move(box["x"] + box["width"] * 0.3, y, steps=8)
            page.mouse.up()
            start = page.evaluate("tracks[0].clips[0].trimStart")
            assert 0.2 < start < 0.4, start

            right = box["x"] + box["width"] - 4                      # правый край -- тянем влево
            page.mouse.move(right, y)
            assert wave.evaluate("el => el.style.cursor") == "ew-resize"
            page.mouse.down()
            page.mouse.move(box["x"] + box["width"] * 0.7, y, steps=8)
            page.mouse.up()
            end = page.evaluate("tracks[0].clips[0].trimEnd")
            assert 0.6 < end < 0.8, end
            assert errors == []
        finally:
            browser.close()


def test_playlist_is_deleted_from_the_menu_in_its_header(live_mixer):
    """12.11, владелец: случайно создал плейлист -- и не смог удалить.
    Кнопка была только внизу раскрытого плейлиста; теперь -- ⋯ в шапке."""
    import json as _json

    app_module, user, job, port = live_mixer
    base_url = f"http://127.0.0.1:{port}"
    app_module.storage.set_prefs(user.id, {"playlists": [
        {"id": "p1", "name": "Случайный", "items": [{"job": job.id, "file": "vocals.mp3"}]}]})
    with sync_playwright() as pw:
        executable = _chromium_executable(pw)
        try:
            browser = pw.chromium.launch(executable_path=executable, args=["--no-sandbox"])
        except Exception as error:
            pytest.skip(f"браузер playwright не поднялся: {error}")
        try:
            context = browser.new_context(viewport={"width": 390, "height": 800})
            context.add_cookies([{"name": "uid", "value": app_module.signer.dumps(user.id), "url": base_url}])
            page = context.new_page()
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.on("dialog", lambda d: d.accept())
            page.goto(f"{base_url}/studio", wait_until="networkidle")
            page.locator('[data-favtab="lists"]').first.click()
            more = page.locator('[data-plmenu="p1"]')
            more.wait_for(state="visible", timeout=8000)
            more.click()
            page.locator('#menu [data-plremove="p1"]').click()
            page.wait_for_function("!document.querySelector('[data-pl=\"p1\"]')", timeout=5000)
            assert page.locator("#menu").is_hidden()
            saved = app_module.storage.prefs(user.id).get("playlists")
            assert saved == [], _json.dumps(saved, ensure_ascii=False)
            assert errors == []
        finally:
            browser.close()


def test_concert_songs_are_prepared_in_the_mixer_and_switch_instantly(live_mixer):
    """12.11, владелец: «на концерте нужно быстрое переключение между
    мультитреками». Песня сводится в мультитреке («🎤 В концерт») и ложится
    в память телефона; /concert играет её сразу, ⏭ и стрелка (педаль) --
    следующая встаёт на старт."""
    app_module, user, job, port = live_mixer
    base_url = f"http://127.0.0.1:{port}"
    with sync_playwright() as pw:
        executable = _chromium_executable(pw)
        try:
            browser = pw.chromium.launch(executable_path=executable,
                                         args=["--no-sandbox", "--autoplay-policy=no-user-gesture-required"])
        except Exception as error:
            pytest.skip(f"браузер playwright не поднялся: {error}")
        try:
            context = browser.new_context(viewport={"width": 390, "height": 800})
            context.add_cookies([{"name": "uid", "value": app_module.signer.dumps(user.id), "url": base_url}])
            page = context.new_page()
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            for name in ("vocals.mp3", "drums.mp3"):
                page.goto(f"{base_url}/studio/mix/{job.id}?file={name}", wait_until="networkidle")
                page.wait_for_function("typeof tracks !== 'undefined' && tracks.length > 0")
                page.locator("#exportMix").click()
                page.locator("#exportConcert").click()
                page.wait_for_function("(document.getElementById('toast').textContent || '').includes('в концерт')", timeout=20000)
            saved = page.evaluate("JSON.parse(localStorage.getItem('naslux.concert'))")
            assert len(saved) == 2 and saved[0]["seconds"] >= 1
            page.goto(f"{base_url}/concert", wait_until="networkidle")
            page.locator(".cc-item").nth(1).wait_for(state="visible", timeout=5000)
            first = page.locator("#ccNow").text_content()
            page.locator("#ccPlay").click()
            page.wait_for_function("document.getElementById('ccPlay').textContent === '⏸'", timeout=5000)
            page.locator("#ccNextBtn").click()
            page.wait_for_function(f"document.getElementById('ccNow').textContent !== {first!r}", timeout=3000)
            assert page.locator("#ccPlay").text_content() == "▶"       # следующая ждёт ▶
            page.keyboard.press("ArrowLeft")                             # педаль: назад
            page.wait_for_function(f"document.getElementById('ccNow').textContent === {first!r}", timeout=3000)
            assert page.locator("#ccPos").text_content() == "Песня 1 из 2"
            assert errors == []
        finally:
            browser.close()


def test_concert_deleting_an_earlier_song_keeps_the_playing_one_current(live_mixer):
    """Регресс: сет-лист A,B,C,D, выбрали C (3-я), ведущий на ходу убирает уже
    отыгранную A (1-ю) из списка -- «сейчас играет» должно остаться C, а не
    молча подмениться на D, которая встала на её старое место в массиве."""
    app_module, user, job, port = live_mixer
    base_url = f"http://127.0.0.1:{port}"
    with sync_playwright() as pw:
        executable = _chromium_executable(pw)
        try:
            browser = pw.chromium.launch(executable_path=executable, args=["--no-sandbox"])
        except Exception as error:
            pytest.skip(f"браузер playwright не поднялся: {error}")
        try:
            context = browser.new_context(viewport={"width": 390, "height": 800})
            page = context.new_page()
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.on("dialog", lambda d: d.accept())
            page.goto(f"{base_url}/", wait_until="networkidle")
            songs = [{"id": letter, "title": letter, "job": "x", "file": "x.mp3", "seconds": 10}
                     for letter in "ABCD"]
            page.evaluate("(songs) => localStorage.setItem('naslux.concert', JSON.stringify(songs))", songs)
            page.goto(f"{base_url}/concert", wait_until="networkidle")
            page.locator(".cc-item").nth(3).wait_for(state="visible", timeout=5000)
            page.locator('[data-pick="2"]').click()                      # выбрали C
            page.wait_for_function("document.getElementById('ccNow').textContent === 'C'")
            page.locator('[data-del="0"]').click()                       # убрали A
            page.wait_for_function("document.querySelectorAll('.cc-item').length === 3")
            assert page.locator("#ccNow").text_content() == "C"
            assert page.locator("#ccPos").text_content() == "Песня 2 из 3"
            assert errors == []
        finally:
            browser.close()
