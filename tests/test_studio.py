"""Студия: оплата с баланса, подписанные ссылки воркера, ход задачи на RunPod."""

from __future__ import annotations

import sys

import pytest


@pytest.fixture
def studio_app(tmp_path, monkeypatch):
    monkeypatch.setenv("MIDI2TAB_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("MIDI2TAB_SECRET", "test-secret")
    monkeypatch.setenv("RUNPOD_API_KEY", "rp-test")
    monkeypatch.setenv("NASLUX_WORKER_ENDPOINT", "ep1")
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
    return client.post("/api/studio", data={"mode": mode, **extra},
                       files={"file": ("песня.mp3", b"ID3fake-audio", "audio/mpeg")})


def test_start_charges_balance_and_passes_signed_links(studio_app):
    app_module, client, user, submitted = studio_app
    app_module.storage.add_balance(user.id, 100)

    response = _start(client, lyrics="Строка", audio_influence="0.3", style_influence="0.8",
                      weirdness="0.5", preset="rock")
    assert response.status_code == 200, response.text
    job_id = response.json()["jobId"]

    assert app_module.storage.user(user.id).balance == pytest.approx(51)
    job = app_module.storage.job(job_id)
    assert job.settings["charged"] == 49 and job.counted
    [(sent_id, data)] = submitted
    assert sent_id == job_id
    assert data["mode"] == "restyle" and data["lyrics"] == "Строка"
    assert data["audio_influence"] == pytest.approx(0.3)
    assert data["style_influence"] == pytest.approx(0.8)
    assert data["weirdness"] == pytest.approx(0.5)
    assert "alternative rock" in data["prompt"]
    assert f"/api/studio/source/{job_id}?e=" in data["audio_url"]
    assert f"/api/studio/upload/{job_id}?e=" in data["upload_url"]


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
    app_module.storage.add_balance(user.id, 100)
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
    assert app_module.storage.user(user.id).balance == pytest.approx(100 - 49 - 19)
    assert submitted[-1][0] == child and submitted[-1][1]["mode"] == "stems"
    source = submitted[-1][1]["audio_url"].split("testserver", 1)[1]
    assert type(client)(app_module.app).get(source).content == b"new-version"
    listed = {j["id"]: j for j in client.get("/api/studio").json()["jobs"]}
    assert listed[child]["title"].endswith("новая версия")


def test_studio_pack_is_credited_spent_first_and_refunded(studio_app, monkeypatch):
    """Пакет генераций: зачисляется по оплате, тратится раньше баланса, при сбое возвращается."""
    app_module, client, user, submitted = studio_app
    from web import billing

    billing.apply_plan(app_module.storage, user.id, "studio10")
    app_module.storage.add_balance(user.id, 100)
    assert app_module.storage.user(user.id).studio_credits == 10

    job_id = _start(client).json()["jobId"]
    assert app_module.storage.user(user.id).studio_credits == 9
    assert app_module.storage.user(user.id).balance == pytest.approx(100)   # деньги не тронуты

    # Разделение на партии из пакета не берётся -- только с баланса
    _start(client, mode="stems")
    assert app_module.storage.user(user.id).studio_credits == 9
    assert app_module.storage.user(user.id).balance == pytest.approx(81)

    runner = app_module.studio.StudioRunner(app_module.storage, app_module.DATA_DIR)
    runner._fail(job_id, "видеокарта упала")
    assert app_module.storage.user(user.id).studio_credits == 10
    assert "вернули в пакет" in app_module.storage.job(job_id).error
    assert app_module.storage.user(user.id).balance == pytest.approx(81)


def test_pack_lets_start_without_balance(studio_app):
    app_module, client, user, submitted = studio_app
    app_module.storage.add_studio_credits(user.id, 1)
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
    app_module.storage.add_balance(user.id, 100)
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


def test_restyle_goes_to_mureka_and_needs_lyrics(studio_app, monkeypatch):
    """С ключом Mureka переделка идёт к ней; без текста песни не запускается."""
    app_module, client, user, submitted = studio_app
    monkeypatch.setenv("MUREKA_API_KEY", "mk-test")
    monkeypatch.setattr(app_module.studio, "RESTYLE_OPEN", False)
    app_module.storage.add_balance(user.id, 100)
    info = client.get("/api/studio").json()
    assert info["restyleOpen"] is True and info["restyleEngine"] == "mureka"

    assert _start(client).status_code == 400                       # без текста
    response = _start(client, lyrics="[Куплет]\nЗемля в иллюминаторе")
    assert response.status_code == 200, response.text
    job = app_module.storage.job(response.json()["jobId"])
    assert job.settings["engine"] == "mureka"


def test_mureka_runner_uploads_remixes_and_downloads(studio_app, monkeypatch):
    app_module, client, user, submitted = studio_app
    studio = app_module.studio
    monkeypatch.setenv("MUREKA_API_KEY", "mk-test")
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
    assert job.settings["remote"] == {"engine": "mureka", "id": "task7"}
    assert client.get(f"/api/studio/file/{job_id}/restyle_2.mp3").content == b"https://cdn.mureka.ai/b.mp3"


def test_mureka_failure_refunds(studio_app, monkeypatch):
    app_module, client, user, submitted = studio_app
    studio = app_module.studio
    monkeypatch.setenv("MUREKA_API_KEY", "mk-test")
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
    calls = []

    def urlopen(request, timeout=60):
        calls.append(1)
        raise urllib.error.HTTPError("u", 429, "x", {}, io.BytesIO(b'{"error":"insufficient balance"}'))

    monkeypatch.setattr(studio.urllib.request, "urlopen", urlopen)
    with pytest.raises(RuntimeError, match="insufficient balance"):
        studio.mureka_call("GET", "/v1/account/billing")
    assert len(calls) == 1
