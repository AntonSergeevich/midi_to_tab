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

    response = _start(client, lyrics="Строка", strength="0.3", preset="rock")
    assert response.status_code == 200, response.text
    job_id = response.json()["jobId"]

    assert app_module.storage.user(user.id).balance == pytest.approx(51)
    job = app_module.storage.job(job_id)
    assert job.settings["charged"] == 49 and job.counted
    [(sent_id, data)] = submitted
    assert sent_id == job_id
    assert data["mode"] == "restyle" and data["lyrics"] == "Строка"
    assert data["strength"] == pytest.approx(0.3)
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


class _FakeResponse:
    def __init__(self, payload):
        self.payload = payload
        self.text = "{}"

    def json(self):
        return self.payload

    def raise_for_status(self):
        pass


def _fake_runpod(monkeypatch, studio, final):
    calls = []

    def post(url, **kwargs):
        calls.append(("POST", url, kwargs.get("json")))
        return _FakeResponse({"id": "remote1"})

    states = iter([{"status": "IN_QUEUE"}, final])

    def get(url, **kwargs):
        calls.append(("GET", url, None))
        return _FakeResponse(next(states))

    monkeypatch.setattr(studio.requests, "post", post)
    monkeypatch.setattr(studio.requests, "get", get)
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
