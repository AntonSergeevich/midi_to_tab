"""Сверка с эталоном: лист аккордов -> аккорды, меры совпадения, сдвиг строя."""

from web import benchmark as b

SHEET = """Куплет 1
Dm                        F
Ты слишком молод, чтобы звать меня гулять
Bb           A
Тебе 17, а ему все 25
А ты? (Что?)
Вступление: Fm | Db
           Bbm                    Fm/C
"""


def test_sheet_keeps_only_chord_lines():
    assert b.parse_sheet(SHEET) == ["Dm", "F", "Bb", "A", "Fm", "Db", "Bbm", "Fm/C"]
    assert b.majmin("Fm/C") == (5, True) and b.majmin("A#m7") == (10, True) and b.majmin("N") is None


def test_exact_match_and_capo_shift():
    ref = ["Dm", "F", "Bb", "A"] * 4
    exact = [(i * 2.0, i * 2.0 + 2, c) for i, c in enumerate(["Dm", "F", "A#", "A"] * 4)]
    got = b.compare(ref, exact)
    assert got["score"] == 1.0 and got["shift"] == 0 and got["missed"] == []
    # трек на полтона ниже эталона (каподастр/строй): находим сдвиг, «как есть» -- честно низко
    lower = [(a, e, {"Dm": "C#m", "F": "E", "A#": "A", "A": "G#"}[c]) for a, e, c in exact]
    moved = b.compare(ref, lower)
    assert moved["shift"] == 1 and moved["score"] == 1.0 and moved["unshifted"] < 0.5
    # мажор вместо минора -- видно в «не нашли» и «лишние», подписи как в эталоне (Bb, не A#)
    wrong = [(a, e, "D" if c == "Dm" else c) for a, e, c in exact]
    miss = b.compare(ref, wrong)
    assert miss["missed"] == ["Dm"] and miss["extra"] == ["D"] and "Bb" in miss["found"]


def test_reference_endpoints(tmp_path, monkeypatch):
    import os
    import sys

    monkeypatch.setenv("MIDI2TAB_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("MIDI2TAB_SECRET", "s")
    for name in [m for m in sys.modules if m.startswith("web.")]:
        del sys.modules[name]
    from fastapi.testclient import TestClient

    import web.app as app_module
    import web.benchmark as bench

    monkeypatch.setattr(bench, "run_models", lambda path: {
        "main": [(0, 4, "Dm"), (4, 8, "F"), (8, 12, "A#"), (12, 16, "A")],
        "r2": [(0, 16, "D")]})
    monkeypatch.setattr(app_module.studio_runner, "benchmark_later",
                        lambda j, n: app_module.studio_runner.benchmark(j, n) and True)
    with TestClient(app_module.app) as client:
        user = app_module.storage.ensure_user(None)
        client.cookies.set("uid", app_module.signer.dumps(user.id))
        job = app_module.storage.create_job(user.id, "Мальчик.mp3", {"kind": "studio", "mode": "upload"})
        folder = app_module.studio_runner.folder(job.id)
        os.makedirs(folder, exist_ok=True)
        open(os.path.join(folder, "track.mp3"), "wb").close()
        app_module.storage.update_job(job.id, status="done", result={"files": [{"name": "track.mp3"}]})
        assert client.post(f"/api/studio/{job.id}/reference", data={"sheet": "просто текст"}).status_code == 400
        assert client.post(f"/api/studio/{job.id}/reference", data={"sheet": SHEET}).json()["chords"] == 8
        stored = app_module.storage.job(job.id).result["reference"]["track.mp3"]
        assert "Ты слишком" not in str(stored)                 # текст песни не храним
        got = client.get(f"/api/studio/{job.id}/reference").json()
        assert got["scores"]["main"]["score"] > got["scores"]["r2"]["score"] and got["chords"] == 8
        summary = client.get("/api/studio/benchmark").json()
        assert summary["rows"][0]["name"] == "Мальчик.mp3" and set(summary["average"]) == {"main", "r2"}
        # «Пересверить все» -- без повторной вставки листа
        assert client.post("/api/studio/benchmark/rerun").json()["queued"] == 1
        again = client.get(f"/api/studio/{job.id}/reference").json()
        assert not again["pending"] and again["chords"] == 8 and "main" in again["scores"]
