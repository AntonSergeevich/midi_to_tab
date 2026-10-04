"""Студия: оплата с баланса, подписанные ссылки воркера, ход задачи на RunPod."""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture
def studio_app(tmp_path, monkeypatch):
    monkeypatch.setenv("MIDI2TAB_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("MIDI2TAB_SECRET", "test-secret")
    monkeypatch.setenv("RUNPOD_API_KEY", "rp-test")
    monkeypatch.setenv("NASLUX_WORKER_ENDPOINT", "ep1")
    monkeypatch.setenv("NASLUX_COVERS", "0")
    for name in [m for m in sys.modules if m.startswith("web.")]:
        del sys.modules[name]
    from fastapi.testclient import TestClient

    import web.app as app_module

    monkeypatch.setattr(app_module.studio, "RESTYLE_OPEN", True)
    submitted = []
    monkeypatch.setattr(app_module.studio_runner, "submit",
                        lambda job_id, data: submitted.append((job_id, data)))
    with TestClient(app_module.app) as client:
        user = app_module.storage.ensure_user(None)
        client.cookies.set("uid", app_module.signer.dumps(user.id))
        yield app_module, client, user, submitted


def _start(client, mode="restyle", **extra):
    extra.setdefault("rights", "own")
    return client.post("/api/studio", data={"mode": mode, **extra},
                       files={"file": ("песня.mp3", b"ID3fake-audio", "audio/mpeg")})


def test_start_charges_balance_and_passes_signed_links(studio_app):
    app_module, client, user, submitted = studio_app
    app_module.storage.add_balance(user.id, 150)

    response = _start(client, lyrics="Строка", audio_influence="0.3", style_influence="0.8",
                      weirdness="0.5", preset="rock")
    assert response.status_code == 200, response.text
    job_id = response.json()["jobId"]

    assert app_module.storage.user(user.id).balance == pytest.approx(51)
    job = app_module.storage.job(job_id)
    assert job.settings["charged"] == 99 and job.counted
    [(sent_id, data)] = submitted
    assert sent_id == job_id
    assert data["mode"] == "restyle" and data["lyrics"] == "Строка"
    assert data["audio_influence"] == pytest.approx(0.3)
    assert data["style_influence"] == pytest.approx(0.8)
    assert data["weirdness"] == pytest.approx(0.5)
    assert "alternative rock" in data["prompt"]
    assert f"/api/studio/source/{job_id}?e=" in data["audio_url"]
    assert f"/api/studio/upload/{job_id}?e=" in data["upload_url"]
    assert f"/api/studio/mureka/{job_id}?e=" in data["mureka_url"]


def test_start_refuses_without_money_and_keeps_balance(studio_app):
    app_module, client, user, submitted = studio_app
    app_module.storage.add_balance(user.id, 20)

    response = _start(client)
    assert response.status_code == 402
    assert app_module.storage.user(user.id).balance == pytest.approx(20)
    assert submitted == []

    # На разделение (19 ₽) тех же денег хватает
    assert _start(client, mode="stems").status_code == 200
    assert app_module.storage.user(user.id).balance == pytest.approx(1)


def test_unlimited_user_is_not_charged(studio_app):
    app_module, client, user, submitted = studio_app
    app_module.storage.set_flags(user.id, unlimited=True)
    assert _start(client, mode="enrich", track="drums").status_code == 200
    job = app_module.storage.job(submitted[0][0])
    assert job.settings["charged"] == 0


def test_studio_closed_without_runpod_key(studio_app, monkeypatch):
    _app_module, client, _user, _submitted = studio_app
    monkeypatch.delenv("RUNPOD_API_KEY")
    assert client.get("/api/studio").json()["ready"] is False
    assert _start(client).status_code == 503


def test_worker_links_need_valid_signature(studio_app):
    app_module, client, user, submitted = studio_app
    app_module.storage.add_balance(user.id, 100)
    job_id = _start(client).json()["jobId"]
    data = submitted[0][1]
    source_path = data["audio_url"].split("testserver", 1)[1]
    upload_path = data["upload_url"].split("testserver", 1)[1]

    anonymous = type(client)(app_module.app)
    assert anonymous.get(source_path).content == b"ID3fake-audio"
    assert anonymous.get(source_path.replace("&s=", "&s=0")).status_code == 403
    assert anonymous.get(f"/api/studio/source/{job_id}?e=9999999999&s=bad").status_code == 403

    saved = anonymous.post(upload_path, content=b"mp3-bytes", headers={"X-File-Name": "vocals.mp3"})
    assert saved.status_code == 200
    bad = anonymous.post(upload_path, content=b"x", headers={"X-File-Name": "../app.db"})
    assert bad.status_code == 400
    # Ссылка на загрузку не открывает скачивание исходника и наоборот
    assert anonymous.get(upload_path.replace("/upload/", "/source/")).status_code == 403


def _fake_runpod(monkeypatch, studio, final):
    calls = []
    states = iter([{"status": "IN_QUEUE"}, final])

    def call(method, url, body=None, timeout=60):
        calls.append((method, url, body))
        return {"id": "remote1"} if method == "POST" else next(states)

    monkeypatch.setattr(studio, "call", call)
    monkeypatch.setattr(studio, "POLL_SECONDS", 0)
    return calls


def test_runner_finishes_job_with_uploaded_files(studio_app, monkeypatch):
    app_module, client, user, submitted = studio_app
    studio = app_module.studio
    app_module.storage.add_balance(user.id, 100)
    job_id = _start(client, mode="stems").json()["jobId"]
    runner = studio.StudioRunner(app_module.storage, app_module.DATA_DIR)
    for name in ("drums.mp3", "vocals.mp3"):
        with open(f"{runner.folder(job_id)}/{name}", "wb") as f:
            f.write(b"mp3")
    calls = _fake_runpod(monkeypatch, studio, {
        "status": "COMPLETED", "executionTime": 42000, "delayTime": 3000,
        "output": {"ok": True, "files": [{"name": "drums.mp3"}, {"name": "vocals.mp3"}]}})

    app_module.storage.update_job(job_id, settings={**app_module.storage.job(job_id).settings,
                                                    "input": submitted[0][1]})
    runner._run(job_id)

    job = app_module.storage.job(job_id)
    assert job.status == "done", job.error
    assert [f["label"] for f in job.result["files"]] == ["Вокал", "Барабаны"]
    assert job.result["gpuSeconds"] == 42
    assert calls[0][1].endswith("/ep1/run") and calls[0][2]["input"]["mode"] == "stems"
    assert job.settings["remote"] == {"endpoint": "ep1", "id": "remote1"}

    listed = client.get("/api/studio").json()["jobs"][0]
    assert listed["files"][0]["url"] == f"/api/studio/file/{job_id}/vocals.mp3"
    assert client.get(listed["files"][0]["url"]).content == b"mp3"


def test_runner_refunds_failed_job(studio_app, monkeypatch):
    app_module, client, user, submitted = studio_app
    studio = app_module.studio
    app_module.storage.add_balance(user.id, 100)
    job_id = _start(client).json()["jobId"]
    runner = studio.StudioRunner(app_module.storage, app_module.DATA_DIR)
    _fake_runpod(monkeypatch, studio, {"status": "COMPLETED",
                                       "output": {"ok": False, "error": "нет CUDA"}})
    app_module.storage.update_job(job_id, settings={**app_module.storage.job(job_id).settings,
                                                    "input": submitted[0][1]})
    runner._run(job_id)

    job = app_module.storage.job(job_id)
    assert job.status == "error" and "нет CUDA" in job.error and "вернули" in job.error
    assert app_module.storage.user(user.id).balance == pytest.approx(100)
    # Повторный сбой той же задачи второй раз денег не возвращает
    runner._fail(job_id, "ещё раз")
    assert app_module.storage.user(user.id).balance == pytest.approx(100)


def test_studio_jobs_stay_out_of_track_library_and_resume(studio_app, monkeypatch):
    app_module, client, user, submitted = studio_app
    app_module.storage.add_balance(user.id, 100)
    job_id = _start(client, mode="stems").json()["jobId"]
    assert all(j.id != job_id for j in app_module.storage.root_jobs(user.id))

    resumed = []
    monkeypatch.setattr(app_module.studio_runner.pool, "submit",
                        lambda fn, *args: resumed.append(args))
    assert app_module.studio_runner.resume() == 1
    assert resumed == [(job_id,)]


def test_other_user_cannot_download_or_delete(studio_app):
    app_module, client, user, submitted = studio_app
    app_module.storage.add_balance(user.id, 100)
    job_id = _start(client, mode="stems").json()["jobId"]
    folder = app_module.studio_runner.folder(job_id)
    with open(f"{folder}/bass.mp3", "wb") as f:
        f.write(b"mp3")
    app_module.storage.update_job(job_id, status="done")

    stranger = app_module.storage.ensure_user(None)
    client.cookies.clear()
    client.cookies.set("uid", app_module.signer.dumps(stranger.id))
    assert client.get(f"/api/studio/file/{job_id}/bass.mp3").status_code == 404
    assert client.delete(f"/api/studio/{job_id}").status_code == 404


def test_split_finished_restyle_into_stems(studio_app):
    """Готовую переделку можно разделить на партии, не загружая её заново."""
    app_module, client, user, submitted = studio_app
    app_module.storage.add_balance(user.id, 150)
    job_id = _start(client).json()["jobId"]
    with open(f"{app_module.studio_runner.folder(job_id)}/restyle.mp3", "wb") as f:
        f.write(b"new-version")

    # Пока переделка не готова -- делить нечего
    assert client.post(f"/api/studio/{job_id}/stems").status_code == 404
    app_module.storage.update_job(job_id, status="done", result={
        "files": [{"name": "restyle.mp3", "label": "Новая версия"}]})

    response = client.post(f"/api/studio/{job_id}/stems")
    assert response.status_code == 200, response.text
    child = response.json()["jobId"]
    assert app_module.storage.user(user.id).balance == pytest.approx(150 - 99 - 19)
    assert submitted[-1][0] == child and submitted[-1][1]["mode"] == "stems"
    source = submitted[-1][1]["audio_url"].split("testserver", 1)[1]
    assert type(client)(app_module.app).get(source).content == b"new-version"
    listed = {j["id"]: j for j in client.get("/api/studio").json()["jobs"]}
    assert listed[child]["title"].endswith("новая версия")


def test_studio_pack_is_credited_spent_first_and_refunded(studio_app, monkeypatch):
    """Кредиты: зачисляются по оплате, тратятся раньше баланса, при сбое возвращаются."""
    app_module, client, user, submitted = studio_app
    from web import billing

    billing.apply_plan(app_module.storage, user.id, "studio10")
    app_module.storage.add_balance(user.id, 100)
    assert app_module.storage.user(user.id).studio_credits == 100

    job_id = _start(client).json()["jobId"]
    assert app_module.storage.user(user.id).studio_credits == 60             # переделка -- 40
    assert app_module.storage.user(user.id).balance == pytest.approx(100)   # деньги не тронуты

    _start(client, mode="stems")                                            # партии -- 3 кредита
    assert app_module.storage.user(user.id).studio_credits == 57
    assert app_module.storage.user(user.id).balance == pytest.approx(100)

    runner = app_module.studio.StudioRunner(app_module.storage, app_module.DATA_DIR)
    runner._fail(job_id, "видеокарта упала")
    assert app_module.storage.user(user.id).studio_credits == 97
    assert "вернули в пакет" in app_module.storage.job(job_id).error


def test_subscription_plans_grant_days_and_credits(studio_app):
    """Подписки: дни без лимита на разборы плюс кредиты Студии; допы -- только кредиты."""
    app_module, client, user, submitted = studio_app
    from web import billing

    billing.apply_plan(app_module.storage, user.id, "studio_month")
    fresh = app_module.storage.user(user.id)
    assert fresh.subscribed and fresh.studio_credits == 300
    billing.apply_plan(app_module.storage, user.id, "studio30")
    assert app_module.storage.user(user.id).studio_credits == 600
    # «Сохранить голос» дешевле remix: 20 кредитов против 40
    assert app_module.studio.credit_cost("restyle", True) == 20
    assert app_module.studio.credit_cost("restyle") == 40


def test_pack_lets_start_without_balance(studio_app):
    app_module, client, user, submitted = studio_app
    app_module.storage.add_studio_credits(user.id, 5)
    assert _start(client, mode="enrich", track="bass").status_code == 200
    assert _start(client).status_code == 402          # пакет кончился, баланса нет
    assert client.get("/api/studio").json()["studioCredits"] == 0


def test_runpod_requests_carry_own_user_agent(studio_app, monkeypatch):
    """Cloudflare перед RunPod отбивает «Python-urllib» кодом 1010 -- нужен свой User-Agent."""
    app_module, _client, _user, _submitted = studio_app
    studio = app_module.studio
    seen = {}

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return b'{"id": "x"}'

    def urlopen(request, timeout=60):
        seen.update({k.lower(): v for k, v in request.header_items()})
        return _Response()

    monkeypatch.setattr(studio.urllib.request, "urlopen", urlopen)
    assert studio.call("POST", "https://api.runpod.ai/v2/ep/run", {"input": {}}) == {"id": "x"}
    assert "python-urllib" not in seen["user-agent"].lower()
    assert seen["user-agent"].startswith("naslux")


def test_two_variants_are_labelled_and_split_separately(studio_app):
    app_module, client, user, submitted = studio_app
    app_module.storage.add_balance(user.id, 150)
    job_id = _start(client).json()["jobId"]
    assert submitted[0][1]["variants"] == 2
    folder = app_module.studio_runner.folder(job_id)
    for n in (1, 2):
        with open(f"{folder}/restyle_{n}.mp3", "wb") as f:
            f.write(f"v{n}".encode())
    app_module.storage.update_job(job_id, status="done", result={
        "files": [{"name": f"restyle_{n}.mp3", "label": app_module.studio.label_of("restyle", f"restyle_{n}.mp3")}
                  for n in (1, 2)],
        "settings": {"bpm": 136, "key_scale": "G major"}})
    listed = client.get("/api/studio").json()["jobs"][0]
    assert [f["label"] for f in listed["files"]] == ["Вариант 1", "Вариант 2"]
    assert (listed["bpm"], listed["key"]) == (136, "G major")

    child = client.post(f"/api/studio/{job_id}/stems", data={"file": "restyle_2.mp3"}).json()["jobId"]
    source = submitted[-1][1]["audio_url"].split("testserver", 1)[1]
    assert type(client)(app_module.app).get(source).content == b"v2"
    titles = {j["id"]: j["title"] for j in client.get("/api/studio").json()["jobs"]}
    assert titles[child].endswith("вариант 2")


def test_restyle_closed_for_regular_users_open_for_unlimited(studio_app, monkeypatch):
    app_module, client, user, submitted = studio_app
    monkeypatch.setattr(app_module.studio, "RESTYLE_OPEN", False)
    app_module.storage.add_balance(user.id, 100)
    assert client.get("/api/studio").json()["restyleOpen"] is False
    assert _start(client).status_code == 409
    assert app_module.storage.user(user.id).balance == pytest.approx(100)
    assert _start(client, mode="stems").status_code == 200

    app_module.storage.set_flags(user.id, unlimited=True)
    assert client.get("/api/studio").json()["restyleOpen"] is True
    assert _start(client).status_code == 200


def test_restyle_goes_to_mureka_without_lyrics(studio_app, monkeypatch):
    """С ключом Mureka переделка идёт к ней; текст не обязателен -- распознаем сами."""
    app_module, client, user, submitted = studio_app
    monkeypatch.setenv("MUREKA_API_KEY", "mk-test")
    monkeypatch.setenv("NASLUX_RESTYLE_ENGINE", "mureka")
    monkeypatch.setenv("NASLUX_MUREKA_DIRECT", "1")
    monkeypatch.setattr(app_module.studio, "RESTYLE_OPEN", False)
    app_module.storage.add_balance(user.id, 100)
    info = client.get("/api/studio").json()
    assert info["restyleOpen"] is True and info["restyleEngine"] == "mureka"

    response = _start(client)                                      # без текста

    assert response.status_code == 200, response.text
    job = app_module.storage.job(response.json()["jobId"])
    assert job.settings["engine"] == "mureka"


def test_remix_without_lyrics_recognizes_words_first(studio_app, monkeypatch):
    """Текст не вставили -- song/recognize, потом remix по распознанным словам."""
    app_module, client, user, submitted = studio_app
    studio = app_module.studio
    monkeypatch.setenv("MUREKA_API_KEY", "mk-test")
    monkeypatch.setenv("NASLUX_RESTYLE_ENGINE", "mureka")
    monkeypatch.setenv("NASLUX_MUREKA_DIRECT", "1")
    app_module.storage.add_balance(user.id, 100)
    job_id = _start(client).json()["jobId"]
    app_module.storage.update_job(job_id, settings={**app_module.storage.job(job_id).settings,
                                                    "input": submitted[0][1]})
    calls = []
    recognized = {"lyrics_sections": [{"lines": [{"text": "Раз"}, {"text": " "}, {"text": "Два"}]},
                                      {"lines": [{"text": "Три"}]}]}

    def mureka_call(method, path, body=None, timeout=60):
        calls.append((method, path, body))
        if path == "/v1/song/recognize":
            return recognized
        return {"id": "task9"} if method == "POST" else {
            "status": "succeeded", "choices": [{"url": "https://cdn.mureka.ai/a.mp3"}]}

    monkeypatch.setattr(studio, "mureka_call", mureka_call)
    monkeypatch.setattr(studio, "mureka_source", lambda source, folder: source)
    monkeypatch.setattr(studio, "mureka_upload", lambda path, purpose: calls.append(
        ("UPLOAD", purpose, "")) or f"up-{purpose}")
    monkeypatch.setattr(studio, "download", lambda url, target: open(target, "wb").write(b"x"))
    monkeypatch.setattr(studio, "POLL_SECONDS", 0)

    studio.StudioRunner(app_module.storage, app_module.DATA_DIR)._run(job_id)

    job = app_module.storage.job(job_id)
    assert job.status == "done", job.error
    assert [c[1] for c in calls[:4]] == ["audio", "/v1/song/recognize", "remix", "/v1/song/remix"]
    assert calls[1][2] == {"upload_audio_id": "up-audio"}
    assert calls[3][2]["lyrics"] == "[Verse]\nРаз\nДва\n\n[Verse]\nТри"
    assert job.result["lyrics"].startswith("[Verse]")


def test_mureka_runner_uploads_remixes_and_downloads(studio_app, monkeypatch):
    app_module, client, user, submitted = studio_app
    studio = app_module.studio
    monkeypatch.setenv("MUREKA_API_KEY", "mk-test")
    monkeypatch.setenv("NASLUX_RESTYLE_ENGINE", "mureka")
    monkeypatch.setenv("NASLUX_MUREKA_DIRECT", "1")
    app_module.storage.add_balance(user.id, 100)
    job_id = _start(client, lyrics="Строка").json()["jobId"]
    app_module.storage.update_job(job_id, settings={**app_module.storage.job(job_id).settings,
                                                    "input": submitted[0][1]})
    calls = []
    states = iter([{"status": "running"},
                   {"status": "succeeded", "model": "mureka-9", "choices": [
                       {"url": "https://cdn.mureka.ai/a.mp3"}, {"url": "https://cdn.mureka.ai/b.mp3"}]}])

    def mureka_call(method, path, body=None, timeout=60):
        calls.append((method, path, body))
        return {"id": "task7"} if method == "POST" else next(states)

    monkeypatch.setattr(studio, "mureka_call", mureka_call)
    monkeypatch.setattr(studio, "mureka_source", lambda source, folder: source)
    monkeypatch.setattr(studio, "mureka_upload", lambda path, purpose: calls.append(
        ("UPLOAD", purpose, path.rsplit("/", 1)[-1])) or "up1")
    monkeypatch.setattr(studio, "download", lambda url, target: open(target, "wb").write(url.encode()))
    monkeypatch.setattr(studio, "POLL_SECONDS", 0)

    runner = studio.StudioRunner(app_module.storage, app_module.DATA_DIR)
    runner._run(job_id)

    job = app_module.storage.job(job_id)
    assert job.status == "done", job.error
    assert calls[0] == ("UPLOAD", "remix", "source.mp3")
    assert calls[1][1] == "/v1/song/remix"
    assert calls[1][2]["upload_audio_id"] == "up1" and calls[1][2]["lyrics"] == "Строка"
    assert calls[1][2]["n"] == 2 and "nu metal" in calls[1][2]["prompt"]
    assert [f["label"] for f in job.result["files"]] == ["Вариант 1", "Вариант 2"]
    assert job.settings["remote"] == {"engine": "mureka", "id": "task7", "kind": "song"}
    assert client.get(f"/api/studio/file/{job_id}/restyle_2.mp3").content == b"https://cdn.mureka.ai/b.mp3"


def test_mureka_failure_refunds(studio_app, monkeypatch):
    app_module, client, user, submitted = studio_app
    studio = app_module.studio
    monkeypatch.setenv("MUREKA_API_KEY", "mk-test")
    monkeypatch.setenv("NASLUX_RESTYLE_ENGINE", "mureka")
    monkeypatch.setenv("NASLUX_MUREKA_DIRECT", "1")
    app_module.storage.add_balance(user.id, 100)
    job_id = _start(client, lyrics="Строка").json()["jobId"]
    app_module.storage.update_job(job_id, settings={**app_module.storage.job(job_id).settings,
                                                    "input": submitted[0][1]})
    monkeypatch.setattr(studio, "mureka_source", lambda source, folder: source)
    monkeypatch.setattr(studio, "mureka_upload", lambda path, purpose: "up1")

    def mureka_call(method, path, body=None, timeout=60):
        return {"id": "t"} if method == "POST" else {"status": "failed", "failed_reason": "copyright"}

    monkeypatch.setattr(studio, "mureka_call", mureka_call)
    studio.StudioRunner(app_module.storage, app_module.DATA_DIR)._run(job_id)
    job = app_module.storage.job(job_id)
    assert job.status == "error" and "copyright" in job.error
    assert app_module.storage.user(user.id).balance == pytest.approx(100)


def test_stems_need_runpod_even_with_mureka(studio_app, monkeypatch):
    app_module, client, user, submitted = studio_app
    monkeypatch.setenv("MUREKA_API_KEY", "mk-test")
    monkeypatch.setenv("NASLUX_RESTYLE_ENGINE", "mureka")
    monkeypatch.setenv("NASLUX_MUREKA_DIRECT", "1")
    monkeypatch.delenv("RUNPOD_API_KEY")
    app_module.storage.add_balance(user.id, 100)
    assert _start(client, mode="stems").status_code == 503
    assert _start(client, lyrics="Строка").status_code == 200


def test_mureka_waits_when_concurrency_limit_is_busy(studio_app, monkeypatch):
    """429 от Mureka (занят единственный слот Trial) -- ждать, а не падать."""
    import io
    import urllib.error

    app_module, _client, _user, _submitted = studio_app
    studio = app_module.studio
    monkeypatch.setenv("MUREKA_API_KEY", "mk-test")
    monkeypatch.setenv("NASLUX_RESTYLE_ENGINE", "mureka")
    monkeypatch.setenv("NASLUX_MUREKA_DIRECT", "1")
    monkeypatch.setattr(studio, "POLL_SECONDS", 0)
    answers = iter([urllib.error.HTTPError("u", 429, "busy", {}, io.BytesIO(b"limit")), b'{"id": "t1"}'])

    class _Response:
        def __init__(self, raw):
            self.raw = raw

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return self.raw

    def urlopen(request, timeout=60):
        answer = next(answers)
        if isinstance(answer, Exception):
            raise answer
        return _Response(answer)

    monkeypatch.setattr(studio.urllib.request, "urlopen", urlopen)
    assert studio.mureka_call("POST", "/v1/song/remix", {"n": 2}) == {"id": "t1"}


def test_mureka_429_about_money_fails_at_once(studio_app, monkeypatch):
    import io
    import urllib.error

    app_module, _client, _user, _submitted = studio_app
    studio = app_module.studio
    monkeypatch.setenv("MUREKA_API_KEY", "mk-test")
    monkeypatch.setenv("NASLUX_RESTYLE_ENGINE", "mureka")
    monkeypatch.setenv("NASLUX_MUREKA_DIRECT", "1")
    calls = []

    def urlopen(request, timeout=60):
        calls.append(1)
        raise urllib.error.HTTPError("u", 429, "x", {}, io.BytesIO(b'{"error":"insufficient balance"}'))

    monkeypatch.setattr(studio.urllib.request, "urlopen", urlopen)
    with pytest.raises(RuntimeError, match="insufficient balance"):
        studio.mureka_call("GET", "/v1/account/billing")
    assert len(calls) == 1


def test_runpod_restyle_uses_v2_recipe_by_default(studio_app):
    """Без подмешивания исходника (удержание 0) и с силой исходника = крутилке."""
    app_module, client, user, submitted = studio_app
    app_module.storage.add_balance(user.id, 300)
    assert _start(client, audio_influence="0.5").status_code == 200
    assert submitted[-1][1]["raw"] == {"audio_cover_strength": 0.5, "cover_noise_strength": 0.0}
    assert _start(client, audio_influence="0.7", melody="0.4").status_code == 200
    assert submitted[-1][1]["raw"] == {"audio_cover_strength": 0.7, "cover_noise_strength": 0.1}
    assert _start(client, mode="stems").status_code == 200
    assert "raw" not in submitted[-1][1]


def test_engine_follows_mureka_key_unless_forced_to_runpod(studio_app, monkeypatch):
    """Ключ Mureka на сервере -- переделка идёт к ней; NASLUX_RESTYLE_ENGINE=runpod -- назад."""
    app_module, client, user, submitted = studio_app
    monkeypatch.setenv("MUREKA_API_KEY", "mk-test")
    monkeypatch.delenv("NASLUX_RESTYLE_ENGINE", raising=False)
    assert client.get("/api/studio").json()["restyleEngine"] == "mureka"
    monkeypatch.setenv("NASLUX_RESTYLE_ENGINE", "runpod")
    app_module.storage.add_balance(user.id, 150)
    assert client.get("/api/studio").json()["restyleEngine"] == "runpod"
    job_id = _start(client).json()["jobId"]            # без текста -- для RunPod можно
    assert app_module.storage.job(job_id).settings["engine"] == "runpod"


def test_html_error_page_is_shortened_to_its_title(studio_app):
    app_module, *_ = studio_app
    page = '<!doctype html><html lang="en"><head><title>Service unavailable | Mureka</title>'
    assert app_module.studio.short_error(page) == "страница «Service unavailable | Mureka»"
    assert app_module.studio.short_error('{"error": "x"}') == '{"error": "x"}'


def _mureka_direct(monkeypatch, studio, answers):
    """Подменить вызовы Mureka: answers -- ответы на GET query по очереди."""
    calls = []
    queue = iter(answers)

    def mureka_call(method, path, body=None, timeout=60):
        calls.append((method, path, body))
        return {"id": "t9"} if method == "POST" else next(queue)

    monkeypatch.setattr(studio, "mureka_call", mureka_call)
    monkeypatch.setattr(studio, "download", lambda url, target: open(target, "wb").write(url.encode()))
    monkeypatch.setattr(studio, "POLL_SECONDS", 0)
    return calls


def _create(client, **extra):
    return client.post("/api/studio", data={"mode": "create", **extra})


def test_create_song_from_scratch_without_file(studio_app, monkeypatch):
    """Песня с нуля: без файла; с текстом -- song/generate на mureka-9, 2 версии."""
    app_module, client, user, submitted = studio_app
    studio = app_module.studio
    monkeypatch.setenv("MUREKA_API_KEY", "mk-test")
    monkeypatch.setenv("NASLUX_MUREKA_DIRECT", "1")
    app_module.storage.add_balance(user.id, 100)
    response = _create(client, title="Для Саши", prompt="pop rock, birthday", lyrics="[Verse]\nС днём")
    assert response.status_code == 200, response.text
    job_id = response.json()["jobId"]
    assert app_module.storage.user(user.id).balance == pytest.approx(51)
    job = app_module.storage.job(job_id)
    assert job.filename == "Для Саши" and job.settings["engine"] == "mureka"
    app_module.storage.update_job(job_id, settings={**job.settings, "input": submitted[-1][1]})

    calls = _mureka_direct(monkeypatch, studio, [
        {"status": "succeeded", "choices": [{"url": "https://cdn/1.mp3", "duration": 95000},
                                            {"url": "https://cdn/2.mp3"}]}])
    studio.StudioRunner(app_module.storage, app_module.DATA_DIR)._run(job_id)
    job = app_module.storage.job(job_id)
    assert job.status == "done", job.error
    assert calls[0][1] == "/v1/song/generate"
    assert calls[0][2]["model"] == "mureka-9" and calls[0][2]["n"] == 2
    assert calls[1][1] == "/v1/song/query/t9"
    assert [f["name"] for f in job.result["files"]] == ["create_1.mp3", "create_2.mp3"]
    assert job.result["files"][0]["label"] == "Вариант 1" and job.result["files"][0]["seconds"] == 95


def test_create_without_lyrics_is_instrumental(studio_app, monkeypatch):
    app_module, client, user, submitted = studio_app
    studio = app_module.studio
    monkeypatch.setenv("MUREKA_API_KEY", "mk-test")
    monkeypatch.setenv("NASLUX_MUREKA_DIRECT", "1")
    app_module.storage.set_flags(user.id, unlimited=True)
    job_id = _create(client, preset="lofi").json()["jobId"]
    job = app_module.storage.job(job_id)
    app_module.storage.update_job(job_id, settings={**job.settings, "input": submitted[-1][1]})
    calls = _mureka_direct(monkeypatch, studio, [{"status": "succeeded",
                                                   "choices": [{"url": "https://cdn/i.mp3"}]}])
    studio.StudioRunner(app_module.storage, app_module.DATA_DIR)._run(job_id)
    assert calls[0][1] == "/v1/instrumental/generate" and "lo-fi" in calls[0][2]["prompt"]
    assert calls[1][1] == "/v1/instrumental/query/t9"
    assert app_module.storage.job(job_id).status == "done"


def test_create_needs_mureka(studio_app, monkeypatch):
    app_module, client, user, submitted = studio_app
    monkeypatch.delenv("MUREKA_API_KEY", raising=False)
    app_module.storage.add_balance(user.id, 100)
    assert _create(client, prompt="rock").status_code == 503
    assert client.get("/api/studio").json()["createOpen"] is False


def test_restyle_pack_refund_returns_two(studio_app, monkeypatch):
    app_module, client, user, submitted = studio_app
    app_module.storage.add_studio_credits(user.id, 40)
    job_id = _start(client).json()["jobId"]
    assert app_module.storage.user(user.id).studio_credits == 0
    assert app_module.storage.job(job_id).settings["charged_credit"] == 40
    app_module.studio.StudioRunner(app_module.storage, app_module.DATA_DIR)._fail(job_id, "сбой")
    assert app_module.storage.user(user.id).studio_credits == 40


def test_lyrics_are_written_by_mureka_with_daily_limit(studio_app, monkeypatch):
    app_module, client, user, submitted = studio_app
    studio = app_module.studio
    monkeypatch.setenv("MUREKA_API_KEY", "mk-test")
    asked = []
    monkeypatch.setattr(studio, "mureka_call", lambda method, path, body=None, timeout=60: (
        asked.append((path, body)) or {"title": "С днём рождения", "lyrics": "[Verse]\nЭй"}))
    monkeypatch.setattr(studio, "LYRICS_PER_DAY", 2)
    first = client.post("/api/studio/lyrics", data={"prompt": "поздравление для Саши"})
    assert first.status_code == 200 and first.json()["title"] == "С днём рождения"
    assert asked[0] == ("/v1/lyrics/generate", {"prompt": "поздравление для Саши"})
    assert client.post("/api/studio/lyrics", data={"prompt": "ещё"}).status_code == 200
    assert client.post("/api/studio/lyrics", data={"prompt": "и ещё"}).status_code == 429


def test_relay_route_for_upload_and_results(studio_app, monkeypatch):
    """Через ретранслятор: загрузка по подписанной ссылке, результаты -- на адрес загрузки."""
    app_module, client, user, submitted = studio_app
    studio = app_module.studio
    monkeypatch.setenv("MUREKA_API_KEY", "mk-test")
    monkeypatch.delenv("NASLUX_MUREKA_DIRECT", raising=False)
    tasks = []

    def relay_run(task, timeout=900):
        tasks.append(task)
        if task["op"] == "upload":
            return {"ok": True, "json": {"id": "up5"}}
        return {"ok": True, "bytes": 3}

    monkeypatch.setattr(studio, "relay_run", relay_run)
    task_input = {"mureka_url": "https://naslux.ru/api/studio/mureka/j?e=1&s=x",
                  "upload_url": "https://naslux.ru/api/studio/upload/j?e=1&s=y"}
    assert studio.mureka_upload_for(task_input, "/tmp/x.mp3", "remix") == "up5"
    studio.fetch_result(task_input, "https://cdn.mureka.ai/a.mp3", "/tmp", "restyle_1.mp3")
    assert tasks[0] == {"op": "upload", "source_url": task_input["mureka_url"],
                        "purpose": "remix", "filename": "track.mp3"}
    assert tasks[1] == {"op": "fetch", "url": "https://cdn.mureka.ai/a.mp3",
                        "upload_url": task_input["upload_url"], "name": "restyle_1.mp3"}


def test_studio_version_goes_to_tabs_and_midi(studio_app, monkeypatch):
    """Готовая версия из Студии уходит в обычный разбор: табы, аккорды, MIDI."""
    app_module, client, user, submitted = studio_app
    app_module.storage.add_balance(user.id, 150)
    job_id = _start(client).json()["jobId"]
    folder = app_module.studio_runner.folder(job_id)
    with open(f"{folder}/restyle_1.mp3", "wb") as f:
        f.write(b"song")
    app_module.storage.update_job(job_id, status="done", result={
        "files": [{"name": "restyle_1.mp3", "label": "Вариант 1"}]})
    started = []
    monkeypatch.setattr(app_module.runner, "submit_analysis", lambda jid, path: started.append((jid, path)))

    assert client.post(f"/api/studio/{job_id}/tabs", data={"file": "../app.db"}).status_code == 409
    response = client.post(f"/api/studio/{job_id}/tabs", data={"file": "restyle_1.mp3"})
    assert response.status_code == 200
    tab_job = app_module.storage.job(response.json()["jobId"])
    assert tab_job.settings["separate"] is True and tab_job.settings["studio"] == job_id
    assert open(started[0][1], "rb").read() == b"song"
    assert "Вариант 1" in tab_job.filename


def test_relay_forgets_deleted_endpoint(monkeypatch):
    """Ретранслятор пересоздали на RunPod -- старый id отвечает 404, берём новый."""
    from web import studio

    monkeypatch.delenv("NASLUX_RELAY_ENDPOINT", raising=False)
    monkeypatch.setitem(studio._relay_cache, "id", "old")
    calls = []

    def fake_call(method, url, body=None, timeout=60):
        calls.append(url)
        if url.endswith("/endpoints"):
            return [{"id": "new", "name": "naslux-relay"}]
        if "/old/" in url:
            raise RuntimeError("RunPod ответил 404: not found")
        return {"status": "COMPLETED", "output": {"ok": True}}

    monkeypatch.setattr(studio, "call", fake_call)
    assert studio.relay_run({"op": "call"})["ok"] is True
    assert studio._relay_cache["id"] == "new"


def test_create_passes_voice_to_mureka(studio_app, monkeypatch):
    """Голос: мужской/женский -- параметр gender и слова в описании; дуэт -- только словами."""
    app_module, client, user, submitted = studio_app
    studio = app_module.studio
    monkeypatch.setenv("MUREKA_API_KEY", "mk-test")
    monkeypatch.setenv("NASLUX_MUREKA_DIRECT", "1")
    app_module.storage.add_balance(user.id, 200)
    assert client.get("/api/studio").json()["voices"]["duet"] == "Дуэт"
    for voice, gender in (("female", "female"), ("duet", "female"), ("robot", None)):
        job_id = _create(client, prompt="pop", lyrics="[Verse]\nЛя", voice=voice).json()["jobId"]
        job = app_module.storage.job(job_id)
        app_module.storage.update_job(job_id, settings={**job.settings, "input": submitted[-1][1]})
        calls = _mureka_direct(monkeypatch, studio, [
            {"status": "succeeded", "choices": [{"url": "https://cdn/1.mp3"}]}])
        studio.StudioRunner(app_module.storage, app_module.DATA_DIR)._run(job_id)
        body = calls[0][2]
        assert body.get("gender") == gender
        if voice == "duet":
            # дуэт: пометки партий в тексте + gender=female (так Mureka поёт вдвоём)
            assert "male and female vocals" in body["prompt"] and "(Male) Ля" in body["lyrics"]
        if voice == "robot":
            assert body["prompt"] == "pop"


def test_keep_vocals_restyle_separates_arranges_and_mixes(studio_app, monkeypatch):
    """«Сохранить мой голос»: текст не нужен; Demucs -> вокал, Mureka track/generate
    две аранжировки по очереди, сведение с голосом, минусы в результате."""
    app_module, client, user, submitted = studio_app
    studio = app_module.studio
    monkeypatch.setenv("MUREKA_API_KEY", "mk-test")
    monkeypatch.setenv("NASLUX_RESTYLE_ENGINE", "mureka")
    monkeypatch.setenv("NASLUX_MUREKA_DIRECT", "1")
    app_module.storage.add_balance(user.id, 150)
    response = _start(client, keep_vocals="true", preset="numetal")
    assert response.status_code == 200, response.text
    job_id = response.json()["jobId"]
    job = app_module.storage.job(job_id)
    assert job.settings["keepVocals"] is True and submitted[-1][1]["keep_vocals"] is True
    app_module.storage.update_job(job_id, settings={**job.settings, "input": submitted[-1][1]})
    folder = app_module.studio_runner.folder(job_id)

    runpod_calls = []

    def runpod(method, url, body=None, timeout=60):
        runpod_calls.append((method, url, body))
        if method == "POST":
            return {"id": "rp1"}
        for name in ("vocals", "drums", "bass"):  # воркер присылает партии в папку
            open(f"{folder}/{name}.mp3", "wb").write(name.encode())
        return {"status": "COMPLETED", "output": {"ok": True}}

    monkeypatch.setattr(studio, "call", runpod)
    monkeypatch.setattr(studio, "endpoint_id", lambda: "ep1")
    uploads, mixes = [], []
    monkeypatch.setattr(studio, "mureka_audio", lambda source, folder: uploads.append(source))
    monkeypatch.setattr(studio, "mureka_upload", lambda path, purpose: uploads.append(purpose) or "upV")
    monkeypatch.setattr(studio, "mix_vocals", lambda v, b, t: mixes.append((v, b)) or open(t, "wb").write(b"mix"))
    calls = _mureka_direct(monkeypatch, studio, [
        {"status": "succeeded", "choices": [{"url": "https://cdn/b1.mp3"}]},
        {"status": "succeeded", "choices": [{"url": "https://cdn/b2.mp3"}]}])

    studio.StudioRunner(app_module.storage, app_module.DATA_DIR)._run(job_id)
    job = app_module.storage.job(job_id)
    assert job.status == "done", job.error
    assert runpod_calls[0][2]["input"]["mode"] == "stems"
    assert uploads[-1] == "audio"
    posts = [c for c in calls if c[0] == "POST"]
    assert len(posts) == 2 and all(c[1] == "/v1/track/generate" for c in posts)
    assert posts[0][2]["generate_type"] == "Instrumental" and posts[0][2]["upload_audio_id"] == "upV"
    assert [f["label"] for f in job.result["files"]] == [
        "Вариант 1", "Вариант 2", "Минус 1 — без голоса", "Минус 2 — без голоса"]
    assert len(mixes) == 2 and mixes[0][0].endswith("vocals.mp3")
    import os
    assert not os.path.exists(f"{folder}/drums.mp3")          # лишние партии убраны
    listed = client.get("/api/studio").json()["jobs"][0]
    assert listed["keepVocals"] is True


def test_cover_is_drawn_alongside_and_served(studio_app, monkeypatch):
    """Обложка: заказывается на эндпоинте naslux-cover с названием, текстом и стилем;
    пришедший cover.jpg отдаётся картинкой и попадает в список треков."""
    app_module, client, user, submitted = studio_app
    studio = app_module.studio
    monkeypatch.setenv("NASLUX_COVERS", "1")
    monkeypatch.setenv("NASLUX_COVER_ENDPOINT", "cov1")
    sent = []

    def runpod(method, url, body=None, timeout=60):
        sent.append((url, body))
        return {"status": "COMPLETED", "output": {"ok": True}}

    monkeypatch.setattr(studio, "call", runpod)
    assert studio.make_cover({"upload_url": "https://site/up"}, "Земляне - Трава.mp3",
                             "[Verse]\nСтрока", "nu metal", 7) is True
    assert sent[0][0].endswith("/cov1/runsync")
    assert sent[0][1]["input"]["title"] == "Земляне - Трава.mp3"
    assert sent[0][1]["input"]["upload_url"] == "https://site/up"

    app_module.storage.add_balance(user.id, 100)
    job_id = _start(client, mode="stems").json()["jobId"]
    folder = app_module.studio_runner.folder(job_id)
    open(f"{folder}/cover.jpg", "wb").write(b"\xff\xd8jpeg")
    listed = client.get("/api/studio").json()["jobs"][0]
    assert listed["cover"] == f"/api/studio/file/{job_id}/cover.jpg"
    response = client.get(listed["cover"])
    assert response.headers["content-type"] == "image/jpeg" and response.content == b"\xff\xd8jpeg"
    assert studio.safe_file_name("../cover.jpg") is None


def test_single_voice_strips_duet_marks():
    """Выбран один голос -- пометки (Male)/(Female) из текста убираются, иначе выйдет дуэт."""
    from web import studio

    text = "[Verse]\n(Male) Раз\n(Female voice, fast rap) Два\n[Chorus]\n(Together) Три"
    voice, lyrics, gender = studio.voice_plan("female", text)
    assert gender == "female" and "(" not in lyrics and "Два" in lyrics
    assert studio.voice_plan("", text)[2] == "female"          # «любой» с партиями -- дуэт
    assert studio.voice_plan("", "[Verse]\nРаз")[2] is None    # без партий -- решает Mureka


def test_upload_needs_rights_mark_and_stores_it(studio_app):
    """Оферта 6.3: без отметки «чья музыка» запись не берём; отметка хранится с заказом."""
    app_module, client, user, submitted = studio_app
    app_module.storage.add_balance(user.id, 100)
    assert _start(client, mode="stems", rights="").status_code == 400
    job_id = _start(client, mode="stems", rights="cover").json()["jobId"]
    stored = app_module.storage.job(job_id).settings["rights"]
    assert stored["kind"] == "cover" and stored["at"] > 0


def test_shift_tempo_and_key_makes_child_version(studio_app, monkeypatch):
    """Темп и тональность: дочерняя работа у трека, считается на своём сервере, бесплатно."""
    app_module, client, user, submitted = studio_app
    studio = app_module.studio
    app_module.storage.add_balance(user.id, 150)
    job_id = _start(client).json()["jobId"]
    folder = app_module.studio_runner.folder(job_id)
    open(f"{folder}/restyle_1.mp3", "wb").write(b"song")
    app_module.storage.update_job(job_id, status="done", result={
        "files": [{"name": "restyle_1.mp3", "label": "Вариант 1"}]})
    balance = app_module.storage.user(user.id).balance
    assert client.post(f"/api/studio/{job_id}/shift", data={"file": "restyle_1.mp3"}).status_code == 400
    child = client.post(f"/api/studio/{job_id}/shift",
                        data={"file": "restyle_1.mp3", "semitones": "-2", "tempo": "90"}).json()["jobId"]
    assert app_module.storage.user(user.id).balance == balance          # бесплатно
    calls = []
    monkeypatch.setattr(studio, "shift_audio", lambda src, dst, st, tempo: calls.append((st, tempo))
                        or open(dst, "wb").write(b"shifted"))
    studio.StudioRunner(app_module.storage, app_module.DATA_DIR)._run(child)
    job = app_module.storage.job(child)
    assert job.status == "done", job.error
    assert calls == [(-2, 0.9)]
    assert job.result["files"][0]["label"] == "−2 полутона, темп 90%"
    listed = {j["id"]: j for j in client.get("/api/studio").json()["jobs"]}
    assert listed[child]["from"] == job_id and "темп 90%" in listed[child]["title"]


def _mureka_env(monkeypatch):
    monkeypatch.setenv("MUREKA_API_KEY", "mk-test")
    monkeypatch.setenv("NASLUX_RESTYLE_ENGINE", "mureka")
    monkeypatch.setenv("NASLUX_MUREKA_DIRECT", "1")


def test_voice_clone_then_song_with_my_voice(studio_app, monkeypatch):
    """Мой голос: согласие обязательно; Demucs -> vocal-clone -> голос в списке;
    песня этим голосом идёт в song/generate с vocal_id и описанием, без gender."""
    app_module, client, user, submitted = studio_app
    studio = app_module.studio
    _mureka_env(monkeypatch)
    monkeypatch.setenv("NASLUX_VOICE_CLONE", "1")
    app_module.storage.add_studio_credits(user.id, 30)
    voice = lambda consent: client.post("/api/studio/voice", data={"name": "Антон", "consent": consent},  # noqa: E731
                                        files={"file": ("me.mp3", b"ID3voice", "audio/mpeg")})
    assert voice("false").status_code == 400
    job_id = voice("true").json()["jobId"]
    assert app_module.storage.user(user.id).studio_credits == 20
    app_module.storage.update_job(job_id, settings={**app_module.storage.job(job_id).settings,
                                                    "input": submitted[-1][1]})
    folder = app_module.studio_runner.folder(job_id)
    runner = studio.StudioRunner(app_module.storage, app_module.DATA_DIR)
    monkeypatch.setattr(runner, "_separate_vocals", lambda jid, st: open(f"{folder}/vocals.mp3", "wb").write(b"v")
                        and f"{folder}/vocals.mp3")
    monkeypatch.setattr(studio.subprocess, "run", lambda cmd, **kw: open(cmd[-1], "wb").write(b"cut"))
    uploads = []
    monkeypatch.setattr(studio, "mureka_upload", lambda path, purpose, api_path="", fields=None, key="id":
                        uploads.append((api_path, fields, key)) or "vocal-777")
    runner._run(job_id)
    assert app_module.storage.job(job_id).status == "done", app_module.storage.job(job_id).error
    assert uploads[0][0] == "/v1/song/vocal-clone" and uploads[0][2] == "vocal_id"
    mine = client.get("/api/studio").json()["myVoices"]
    assert [v["name"] for v in mine] == ["Антон"]

    song = _create(client, prompt="pop", lyrics="[Verse]\nЛя", voice=f"my:{mine[0]['id']}").json()["jobId"]
    job = app_module.storage.job(song)
    app_module.storage.update_job(song, settings={**job.settings, "input": submitted[-1][1]})
    calls = _mureka_direct(monkeypatch, studio, [{"status": "succeeded", "choices": [
        {"url": "https://cdn/1.mp3", "id": "s1", "duration": 90000}]}])
    runner._run(song)
    body = calls[0][2]
    assert body["vocal_id"] == "vocal-777" and body["prompt"] == "pop" and "gender" not in body
    done = app_module.storage.job(song).result["files"][0]
    assert done["mid"] == "s1" and done["ms"] == 90000        # id для продления

    # Продление этой песни: song/extend с конца версии, текст обязателен
    assert client.post(f"/api/studio/{song}/extend", data={"file": "create_1.mp3"}).status_code == 400
    child = client.post(f"/api/studio/{song}/extend",
                        data={"file": "create_1.mp3", "lyrics": "[Verse]\nДальше"}).json()["jobId"]
    app_module.storage.update_job(child, settings={**app_module.storage.job(child).settings,
                                                   "input": submitted[-1][1]})
    calls = _mureka_direct(monkeypatch, studio, [{"status": "succeeded", "choices": [
        {"url": "https://cdn/e.mp3", "id": "s2"}]}])
    runner._run(child)
    assert calls[0][1] == "/v1/song/extend"
    assert calls[0][2] == {"song_id": "s1", "lyrics": "[Verse]\nДальше", "extend_at": 90000}
    assert app_module.storage.job(child).result["files"][0]["label"] == "Продолжение 1"


def test_create_from_reference_sends_reference_without_prompt(studio_app, monkeypatch):
    """Песня «как в образце»: файл -> reference_id; описание Mureka с ним не берёт."""
    app_module, client, user, submitted = studio_app
    studio = app_module.studio
    _mureka_env(monkeypatch)
    app_module.storage.add_balance(user.id, 100)
    response = client.post("/api/studio", data={"mode": "create", "title": "По образцу", "prompt": "rock",
                                                "lyrics": "[Verse]\nЛя", "reference": "true", "rights": "cover"},
                           files={"file": ("образец.mp3", b"ID3ref", "audio/mpeg")})
    assert response.status_code == 200, response.text
    job_id = response.json()["jobId"]
    job = app_module.storage.job(job_id)
    assert job.filename == "По образцу" and job.settings["rights"]["kind"] == "cover"
    app_module.storage.update_job(job_id, settings={**job.settings, "input": submitted[-1][1]})
    monkeypatch.setattr(studio, "mureka_source", lambda source, folder: source)
    monkeypatch.setattr(studio, "mureka_upload", lambda path, purpose: f"ref-{purpose}")
    calls = _mureka_direct(monkeypatch, studio, [{"status": "succeeded", "choices": [{"url": "https://cdn/1.mp3"}]}])
    studio.StudioRunner(app_module.storage, app_module.DATA_DIR)._run(job_id)
    body = calls[0][2]
    assert body["reference_id"] == "ref-reference" and "prompt" not in body


def test_stems_pro_unpacks_parts_and_midi(studio_app, monkeypatch):
    """Глубокое разделение: архивы Mureka -> партии mp3 + MIDI каждой; из MIDI -- табы."""
    import io
    import zipfile

    app_module, client, user, submitted = studio_app
    studio = app_module.studio
    _mureka_env(monkeypatch)
    app_module.storage.add_studio_credits(user.id, 100)
    job_id = _start(client, mode="stems", pro="true").json()["jobId"]
    assert app_module.storage.user(user.id).studio_credits == 40              # 60 кредитов
    job = app_module.storage.job(job_id)
    assert job.settings["engine"] == "mureka"
    app_module.storage.update_job(job_id, settings={**job.settings, "input": submitted[-1][1]})

    def archive(names):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as z:
            for n in names:
                z.writestr(n, b"data")
        return buffer.getvalue()

    monkeypatch.setattr(studio.subprocess, "run", lambda cmd, **kw: open(cmd[-1], "wb").write(b"mp3"))
    monkeypatch.setattr(studio, "mureka_call", lambda m, path, body=None, timeout=60: {
        "zip_url": "https://cdn/stems.zip", "midi_zip_url": "https://cdn/midi.zip"})
    monkeypatch.setattr(studio, "download", lambda url, target: open(target, "wb").write(
        archive(["song/Vocals.wav", "song/Guitar.wav", "song/Strings.wav"]) if "stems" in url
        else archive(["song/guitar.mid", "song/vocal.mid"])))
    studio.StudioRunner(app_module.storage, app_module.DATA_DIR)._run(job_id)
    job = app_module.storage.job(job_id)
    assert job.status == "done", job.error
    assert [f["label"] for f in job.result["files"]] == ["Вокал", "Гитара", "Струнные"]
    assert [m["label"] for m in job.result["midi"]] == ["MIDI · Гитара", "MIDI · Вокал"]
    listed = client.get("/api/studio").json()["jobs"][0]
    assert listed["pro"] and listed["midi"][0]["url"].endswith("/guitar.mid")
    response = client.get(listed["midi"][0]["url"])
    assert response.headers["content-type"] == "audio/midi"
    # табы прямо из MIDI -- без распознавания звука и разделения
    started = []
    monkeypatch.setattr(app_module.runner, "submit_analysis", lambda jid, path: started.append(path))
    tab = client.post(f"/api/studio/{job_id}/tabs", data={"file": "guitar.mid"})
    assert tab.status_code == 200, tab.text
    assert started[0].endswith(".mid")
    assert app_module.storage.job(tab.json()["jobId"]).settings["separate"] is False


def test_upload_goes_straight_to_my_tracks_with_key_and_tempo(studio_app, monkeypatch, tmp_path):
    """Свой трек: бесплатно, сразу в «Мои треки», mp3 для плеера, тональность и темп."""
    import subprocess

    app_module, client, user, submitted = studio_app
    studio = app_module.studio
    wav = tmp_path / "song.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=220:duration=2",
                    str(wav)], check=True)
    assert client.post("/api/studio/upload", files={"file": ("song.wav", wav.read_bytes())}).status_code == 400
    balance = app_module.storage.user(user.id).balance
    job_id = client.post("/api/studio/upload", data={"rights": "own"},
                         files={"file": ("song.wav", wav.read_bytes())}).json()["jobId"]
    assert submitted[-1] == (job_id, {"mode": "upload"})
    assert app_module.storage.user(user.id).balance == balance            # бесплатно
    listed = {j["id"]: j for j in client.get("/api/studio").json()["jobs"]}
    assert listed[job_id]["mode"] == "upload" and listed[job_id]["name"] == "song.wav"

    monkeypatch.setattr(studio, "analyze_audio", lambda path: {
        "v": studio.ANALYSIS_VERSION, "key": "Am", "bpm": 120, "beats": [0.5, 1.0], "downbeats": [0.5], "chords": [[0, 2, "Am"]]})
    runner = studio.StudioRunner(app_module.storage, app_module.DATA_DIR)
    app_module.storage.update_job(job_id, settings={**app_module.storage.job(job_id).settings,
                                                    "input": {"mode": "upload"}})
    runner._run(job_id)
    job = app_module.storage.job(job_id)
    assert job.status == "done", job.error
    assert job.result["files"][0]["name"] == "track.mp3" and job.result["files"][0]["seconds"] == 2
    listed = {j["id"]: j for j in client.get("/api/studio").json()["jobs"]}
    assert listed[job_id]["key"] == "Am" and listed[job_id]["bpm"] == 120
    analysis = client.get(f"/api/studio/{job_id}/analysis?file=track.mp3").json()
    assert analysis["beats"] == [0.5, 1.0] and analysis["chords"] == [[0, 2, "Am"]]
    assert client.get(f"/api/studio/{job_id}/analysis?file=nope.mp3").status_code == 409
    assert client.get(f"/studio/mix/{job_id}").status_code == 200


def test_analysis_starts_in_background_then_is_cached(studio_app, monkeypatch):
    app_module, client, user, submitted = studio_app
    app_module.storage.add_balance(user.id, 150)
    job_id = _start(client).json()["jobId"]
    folder = app_module.studio_runner.folder(job_id)
    open(f"{folder}/restyle_1.mp3", "wb").write(b"song")
    app_module.storage.update_job(job_id, status="done", result={
        "files": [{"name": "restyle_1.mp3", "label": "Вариант 1"}]})
    started = []
    monkeypatch.setattr(app_module.studio_runner, "analyze_later",
                        lambda job, name: started.append((job, name)) or True)
    assert client.get(f"/api/studio/{job_id}/analysis?file=restyle_1.mp3").json() == {"pending": True}
    assert started == [(job_id, "restyle_1.mp3")]
    monkeypatch.setattr(app_module.studio, "analyze_audio", lambda path: {"v": app_module.studio.ANALYSIS_VERSION, "key": "F#m", "bpm": 98})
    app_module.studio_runner.analyze(job_id, "restyle_1.mp3")
    assert client.get(f"/api/studio/{job_id}/analysis?file=restyle_1.mp3").json()["key"] == "F#m"


def test_midi_of_a_ready_stem_without_resplitting(studio_app, monkeypatch):
    """MIDI готовой партии: бесплатно, в фоне, в список MIDI той же работы;
    барабаны -- нельзя (только глубокое разделение)."""
    app_module, client, user, submitted = studio_app
    studio = app_module.studio
    job = app_module.storage.create_job(user.id, "song.mp3", {"kind": "studio", "mode": "stems",
                                                              "title": "Разделение на партии"})
    folder = app_module.studio_runner.folder(job.id)
    import os
    os.makedirs(folder, exist_ok=True)
    for name in ("vocals.mp3", "drums.mp3"):
        open(f"{folder}/{name}", "wb").write(b"x")
    app_module.storage.update_job(job.id, status="done", result={
        "files": [{"name": "vocals.mp3", "label": "Вокал"}, {"name": "drums.mp3", "label": "Барабаны"}]})
    monkeypatch.setattr(app_module.audioin, "available", lambda: (True, ""))
    assert client.post(f"/api/studio/{job.id}/midi", data={"file": "drums.mp3"}).status_code == 400
    started = []
    monkeypatch.setattr(app_module.studio_runner, "transcribe_later", lambda j, n: started.append((j, n)))
    assert client.post(f"/api/studio/{job.id}/midi", data={"file": "vocals.mp3"}).json() == {"pending": True}
    assert started == [(job.id, "vocals.mp3")]

    # сама расшифровка -- в фоне, результат -- в midi работы
    runner = studio.StudioRunner(app_module.storage, app_module.DATA_DIR)
    monkeypatch.setattr("midi2tab.audioin.transcribe",
                        lambda src, dst, *a, **k: open(dst, "wb").write(b"MThd") and (dst, 10))
    runner.transcribe_later(job.id, "vocals.mp3")
    runner.midi_pool.shutdown(wait=True)
    listed = {j["id"]: j for j in client.get("/api/studio").json()["jobs"]}
    assert [m["name"] for m in listed[job.id]["midi"]] == ["vocals.mid"]
    assert listed[job.id]["midi"][0]["label"] == "Вокал"
    assert client.get(listed[job.id]["midi"][0]["url"]).content == b"MThd"


def test_shift_all_tracks_of_a_multitrack_at_once(studio_app, monkeypatch):
    """Мультитрек: «Тон и темп» сдвигает все партии разом, имена и подписи те же."""
    import os

    app_module, client, user, submitted = studio_app
    studio = app_module.studio
    job = app_module.storage.create_job(user.id, "song.mp3", {"kind": "studio", "mode": "stems",
                                                              "title": "Разделение на партии"})
    folder = app_module.studio_runner.folder(job.id)
    os.makedirs(folder, exist_ok=True)
    for name in ("vocals.mp3", "bass.mp3"):
        open(f"{folder}/{name}", "wb").write(name.encode())
    app_module.storage.update_job(job.id, status="done", result={
        "files": [{"name": "vocals.mp3", "label": "Вокал"}, {"name": "bass.mp3", "label": "Бас"}]})
    child = client.post(f"/api/studio/{job.id}/shift",
                        data={"file": "*", "semitones": "2", "tempo": "80"}).json()["jobId"]
    calls = []
    monkeypatch.setattr(studio, "shift_audio", lambda src, dst, st, tempo: calls.append(
        (open(src, "rb").read(), os.path.basename(dst), st, tempo)) or open(dst, "wb").write(b"shifted"))
    studio.StudioRunner(app_module.storage, app_module.DATA_DIR)._run(child)
    done = app_module.storage.job(child)
    assert done.status == "done", done.error
    assert calls == [(b"vocals.mp3", "vocals.mp3", 2, 0.8), (b"bass.mp3", "bass.mp3", 2, 0.8)]
    listed = {j["id"]: j for j in client.get("/api/studio").json()["jobs"]}
    assert [f["label"] for f in listed[child]["files"]] == ["Вокал", "Бас"]
    assert listed[child]["shift"] == {"of": job.id, "file": "*", "semitones": 2, "tempo": 0.8}


def test_mixer_chords_listen_to_parts_without_vocals_and_drums(studio_app, monkeypatch):
    """Аккорды мультитрека по разделённому треку -- по сведению партий без
    голоса и барабанов (harmony.wav), с кешем как у обычного разбора."""
    import os
    import subprocess

    app_module, client, user, submitted = studio_app
    job = app_module.storage.create_job(user.id, "song.mp3", {"kind": "studio", "mode": "stems",
                                                              "title": "Разделение на партии"})
    folder = app_module.studio_runner.folder(job.id)
    os.makedirs(folder, exist_ok=True)
    for name, freq in (("vocals", 880), ("drums", 60), ("bass", 110), ("guitar", 330)):
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", f"sine=frequency={freq}:duration=1",
                        f"{folder}/{name}.mp3"], check=True)
    app_module.storage.update_job(job.id, status="done", result={"files": [
        {"name": f"{n}.mp3", "label": n} for n in ("vocals", "drums", "bass", "guitar")]})
    mixed = []
    real_mix = app_module.separate.harmonic_mix
    monkeypatch.setattr(app_module.separate, "harmonic_mix",
                        lambda parts, out: mixed.append(sorted(parts)) or real_mix(parts, out))
    started = []
    monkeypatch.setattr(app_module.studio_runner, "analyze_later", lambda j, n: started.append(n) or True)
    assert client.get(f"/api/studio/{job.id}/analysis?file=harmony").json() == {"pending": True}
    assert mixed == [["bass.mp3", "drums", "guitar.mp3", "vocals"]] and started == ["harmony.wav"]
    assert os.path.isfile(f"{folder}/harmony.wav")
    client.get(f"/api/studio/{job.id}/analysis?file=harmony")      # второй раз -- без пересведения
    assert len(mixed) == 1


def test_mixer_tempo_comes_from_the_whole_track_not_harmony(studio_app, monkeypatch):
    """Аккорды -- по партиям без барабанов, а темп и доли -- по треку
    целиком: без барабанов 96 BPM читалось как 129."""
    import os

    app_module, client, user, submitted = studio_app
    studio = app_module.studio
    parent = app_module.storage.create_job(user.id, "song.mp3", {"kind": "studio", "mode": "upload"})
    os.makedirs(app_module.studio_runner.folder(parent.id), exist_ok=True)
    open(os.path.join(app_module.studio_runner.folder(parent.id), "track.mp3"), "wb").close()
    app_module.storage.update_job(parent.id, status="done", result={"files": [{"name": "track.mp3"}]})
    stems = app_module.storage.create_job(user.id, "song.mp3", {"kind": "studio", "mode": "stems",
                                                                "from": parent.id, "sourceFile": "track.mp3"})
    os.makedirs(app_module.studio_runner.folder(stems.id), exist_ok=True)
    open(os.path.join(app_module.studio_runner.folder(stems.id), "harmony.wav"), "wb").close()
    app_module.storage.update_job(stems.id, status="done", result={"files": []})
    found = {"track.mp3": {"v": studio.ANALYSIS_VERSION, "key": "Dm", "bpm": 96,
                           "beats": [0.0, 0.625], "downbeats": [0.0], "chords": [[0, 4, "C"]]},
             "harmony.wav": {"v": studio.ANALYSIS_VERSION, "key": "Dm", "bpm": 129,
                             "beats": [0.0, 0.465], "downbeats": [0.0], "chords": [[0, 4, "Dm"]]}}
    monkeypatch.setattr(studio, "analyze_audio", lambda path: found[os.path.basename(path)])
    result = app_module.studio_runner.analyze(stems.id, "harmony.wav")
    assert result["bpm"] == 96 and result["beats"] == [0.0, 0.625]
    assert result["chords"] == [[0, 4, "Dm"]]                  # аккорды -- по гармонии
    assert app_module.storage.job(parent.id).result["analysis"]["track.mp3"]["bpm"] == 96


def test_own_track_in_mixer_is_kept_with_the_track(studio_app, tmp_path):
    """Своя дорожка мультитрека хранится при треке: перекодирована в mp3,
    видна в списке работ, скачивается, удаляется; чужим -- недоступна."""
    import os
    import subprocess
    import urllib.parse

    app_module, client, user, submitted = studio_app
    job = app_module.storage.create_job(user.id, "song.mp3", {"kind": "studio", "mode": "upload"})
    os.makedirs(app_module.studio_runner.folder(job.id), exist_ok=True)
    app_module.storage.update_job(job.id, status="done", result={"files": []})
    take = tmp_path / "репетиция.m4a"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
                    str(take)], check=True)
    answer = client.post(f"/api/studio/{job.id}/extra", data={"label": "Репетиция", "version": "track.mp3"},
                         files={"file": (take.name, take.read_bytes(), "audio/mp4")})
    assert answer.status_code == 200, answer.text
    extra = answer.json()
    assert extra["label"] == "Репетиция" and extra["for"] == "track.mp3" and extra["name"].endswith(".mp3")
    folder = app_module.studio_runner.folder(job.id)
    assert sorted(os.listdir(folder)) == [extra["name"]]            # исходник загрузки не остаётся
    listed = {j["id"]: j for j in client.get("/api/studio").json()["jobs"]}
    assert [x["label"] for x in listed[job.id]["extras"]] == ["Репетиция"]
    file = client.get(extra["url"])
    assert file.status_code == 200 and "Репетиция" in urllib.parse.unquote(file.headers["content-disposition"])

    from fastapi.testclient import TestClient

    stranger = TestClient(app_module.app)
    stranger.cookies.set("uid", app_module.signer.dumps(app_module.storage.ensure_user(None).id))
    assert stranger.delete(f"/api/studio/{job.id}/extra/{extra['name']}").status_code == 404
    assert stranger.post(f"/api/studio/{job.id}/extra", files={"file": ("a.mp3", b"x")}).status_code == 404
    assert stranger.get(extra["url"]).status_code == 404

    bad = client.post(f"/api/studio/{job.id}/extra", files={"file": ("битый.mp3", b"not audio")})
    assert bad.status_code == 400 and sorted(os.listdir(folder)) == [extra["name"]]
    assert client.post(f"/api/studio/{job.id}/extra", files={"file": ("a.exe", b"x")}).status_code == 400
    assert client.delete(f"/api/studio/{job.id}/extra/{extra['name']}").json() == {"ok": True}
    assert os.listdir(folder) == [] and app_module.storage.job(job.id).result["extras"] == []


def test_own_tempo_replaces_detected_one_everywhere(studio_app, monkeypatch):
    """Музыкант поправил темп (автомат дал 108 вместо 81): ровная сетка от
    его «раз», в строке трека -- его темп; у партий темп хранится на
    исходном треке; bpm=0 возвращает автоопределение."""
    import os

    app_module, client, user, submitted = studio_app
    studio = app_module.studio
    parent = app_module.storage.create_job(user.id, "song.mp3", {"kind": "studio", "mode": "upload"})
    folder = app_module.studio_runner.folder(parent.id)
    os.makedirs(folder, exist_ok=True)
    open(os.path.join(folder, "track.mp3"), "wb").close()
    auto = {"v": studio.ANALYSIS_VERSION, "key": "Dm", "bpm": 108, "beats": [0.3, 0.86, 1.41],
            "downbeats": [0.3], "chords": [[0, 12, "Dm"]]}
    app_module.storage.update_job(parent.id, status="done", result={
        "files": [{"name": "track.mp3", "label": "Оригинал"}], "analysis": {"track.mp3": auto}})

    assert client.post(f"/api/studio/{parent.id}/tempo", data={"file": "track.mp3", "bpm": 500}).status_code == 400
    assert client.post(f"/api/studio/{parent.id}/tempo",
                       data={"file": "track.mp3", "bpm": 81, "start": 1.5}).json() == {"ok": True}
    fixed = client.get(f"/api/studio/{parent.id}/analysis?file=track.mp3").json()
    assert fixed["bpm"] == 81 and fixed["autoBpm"] == 108 and fixed["fixed"] is True
    step = 60 / 81
    assert 1.5 in fixed["downbeats"] and all(abs(b - a - step) < 0.002 for a, b in zip(fixed["beats"], fixed["beats"][1:]))
    assert abs(fixed["downbeats"][1] - fixed["downbeats"][0] - 4 * step) < 0.003
    listed = {j["id"]: j for j in client.get("/api/studio").json()["jobs"]}
    assert listed[parent.id]["bpm"] == 81 and listed[parent.id]["keys"]["track.mp3"]["bpm"] == 81

    # Мультитрек партий правит темп исходного трека
    stems = app_module.storage.create_job(user.id, "song.mp3", {"kind": "studio", "mode": "stems",
                                                                "from": parent.id, "sourceFile": "track.mp3"})
    os.makedirs(app_module.studio_runner.folder(stems.id), exist_ok=True)
    open(os.path.join(app_module.studio_runner.folder(stems.id), "harmony.wav"), "wb").close()
    app_module.storage.update_job(stems.id, status="done", result={"files": [], "analysis": {"harmony.wav": auto}})
    client.post(f"/api/studio/{stems.id}/tempo", data={"file": "harmony", "bpm": 80, "start": 0})
    assert app_module.storage.job(parent.id).result["tempoFix"]["track.mp3"]["bpm"] == 80
    assert client.get(f"/api/studio/{stems.id}/analysis?file=harmony").json()["bpm"] == 80

    client.post(f"/api/studio/{parent.id}/tempo", data={"file": "track.mp3", "bpm": 0})
    back = client.get(f"/api/studio/{parent.id}/analysis?file=track.mp3").json()
    assert back["bpm"] == 108 and "fixed" not in back


def test_cover_of_own_track_takes_chosen_version_as_source(studio_app):
    """«Кавер на этот трек»: исходник «Переделать» -- выбранная версия
    песни, созданной с нуля (у неё нет source.*), без новой загрузки."""
    import os

    app_module, client, user, submitted = studio_app
    app_module.storage.add_balance(user.id, 500)
    song = app_module.storage.create_job(user.id, "Для Натали", {"kind": "studio", "mode": "create"})
    folder = app_module.studio_runner.folder(song.id)
    os.makedirs(folder, exist_ok=True)
    for n, data in (("create_1.mp3", b"ID3first"), ("create_2.mp3", b"ID3second")):
        with open(os.path.join(folder, n), "wb") as out:
            out.write(data)
    app_module.storage.update_job(song.id, status="done", result={"files": [
        {"name": "create_1.mp3", "label": "Версия 1"}, {"name": "create_2.mp3", "label": "Версия 2"}]})

    answer = client.post("/api/studio", data={"mode": "restyle", "again": song.id, "againFile": "create_2.mp3",
                                              "prompt": "панк-рок", "rights": "own"})
    assert answer.status_code == 200, answer.text
    cover = app_module.storage.job(answer.json()["jobId"])
    assert cover.filename == "Для Натали"
    with open(os.path.join(app_module.studio_runner.folder(cover.id), "source.mp3"), "rb") as src:
        assert src.read() == b"ID3second"

    # чужое имя файла -- не путь наружу: без source.* такого исходника нет
    bad = client.post("/api/studio", data={"mode": "restyle", "again": song.id, "againFile": "../../etc/passwd",
                                           "prompt": "панк-рок", "rights": "own"})
    assert bad.status_code == 409


def test_describe_track_gives_style_and_lyrics_once(studio_app, monkeypatch):
    """«Повторить» / «Похожая песня» / «Кавер»: стиль трека -- из song/describe,
    слова -- распознанные (у загруженного) или сохранённые (у созданной).
    Второй запрос -- из кэша, Mureka не зовём."""
    import os
    import subprocess

    app_module, client, user, submitted = studio_app
    studio = app_module.studio
    monkeypatch.setattr(studio, "restyle_engine", lambda: "mureka")
    calls = []

    def fake_call(method, path, body=None, timeout=60):
        calls.append(path)
        assert path == "/v1/song/describe" and body["url"].startswith("data:audio/mp3;base64,")
        return {"genres": ["Pop", "Electropop"], "instrument": ["Piano", "pop"], "tags": ["Anthemic"],
                "description": "A dynamic pop track."}
    monkeypatch.setattr(studio, "mureka_call", fake_call)
    monkeypatch.setattr(studio, "recognize_lyrics", lambda task, path: calls.append("recognize") or "[Verse]\nТишина")
    monkeypatch.setattr(app_module.studio_runner, "describe_later",
                        lambda j, n, url: app_module.studio_runner.describe(j, n, url) and True)

    def make(mode, **extra):
        job = app_module.storage.create_job(user.id, "Тишина.mp3", {"kind": "studio", "mode": mode, **extra})
        folder = app_module.studio_runner.folder(job.id)
        os.makedirs(folder, exist_ok=True)
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
                        f"{folder}/track.mp3"], check=True)
        app_module.storage.update_job(job.id, status="done", result={"files": [{"name": "track.mp3"}]})
        return job

    upload = make("upload")
    assert client.get(f"/api/studio/{upload.id}/describe").json() == {"pending": True}
    found = client.get(f"/api/studio/{upload.id}/describe").json()
    assert found["style"] == "pop, electropop, piano, anthemic"
    assert found["lyrics"] == "[Verse]\nТишина" and calls == ["/v1/song/describe", "recognize"]
    client.get(f"/api/studio/{upload.id}/describe")                     # из кэша
    assert calls == ["/v1/song/describe", "recognize"]

    created = make("create", input={"prompt": "", "lyrics": "Свой текст"})
    client.get(f"/api/studio/{created.id}/describe")
    assert client.get(f"/api/studio/{created.id}/describe").json()["lyrics"] == "Свой текст"
    assert calls.count("recognize") == 1                                  # сохранённые слова не распознаём
    assert client.get(f"/api/studio/{created.id}/describe?file=../x").status_code == 409
