"""Восстановление из бэкапа Beget с объединением (deploy/restore_data.sh + merge_data.py)."""
import json
import os
import subprocess
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from web import auth  # noqa: E402
from web.storage import Storage  # noqa: E402


def build_old(folder: Path) -> dict:
    """База старого сервера (из бэкапа)."""
    db = Storage(str(folder / "app.db"))
    ids = {}
    for key, email in (("owner", "owner@example.com"), ("oleg", "oleg@example.com"), ("ivan", "ivan@example.com")):
        u = db.ensure_user(None)
        db.register(u.id, email, auth.hash_password("старый-пароль-1"))
        ids[key] = u.id
    db.set_flags(ids["owner"], unlimited=True, is_admin=True)
    db.add_balance(ids["oleg"], 50)
    db.add_studio_credits(ids["oleg"], 40)
    yx = db.ensure_user(None)
    db.register(yx.id, "yandex-777@yandex.id", "oauth$x")
    db.link_provider(yx.id, "yandex", "777")
    ids["yx"] = yx.id
    old_job = db.create_job(ids["oleg"], "Старая песня.mp3", {"kind": "studio", "mode": "restyle"})
    (folder / "studio" / old_job.id).mkdir(parents=True)
    (folder / "studio" / old_job.id / "v1.mp3").write_bytes(b"old")
    db.set_prefs(ids["oleg"], {"playlists": [{"id": "p-old", "name": "Старый", "items": []}]})
    (folder / "genapi_key").write_text("УТЁКШИЙ-КЛЮЧ")
    (folder / "models").mkdir()
    (folder / "models" / "big.bin").write_bytes(b"x" * 10)
    ids["old_job"] = old_job.id
    return ids


def build_new(folder: Path) -> dict:
    """База нового сервера: перенос из GetPlatinum, учитель, аноним, вход через Яндекс."""
    db = Storage(str(folder / "app.db"))
    ids = {}
    owner = db.ensure_user(None)
    db.register(owner.id, "owner@example.com", auth.hash_password("новый-пароль-владельца"))
    db.set_flags(owner.id, unlimited=True, is_admin=True)
    oleg = db.ensure_user(None)
    db.register(oleg.id, "OLEG@example.com", auth.hash_password("новый-пароль-олега"))
    db.set_flags(oleg.id, note="Олег · +79110000001 · оплачено 209 ₽ · перенос:getplatinum")
    db.add_balance(oleg.id, 209 - 100)              # возмещение 209, потратил 100
    db.add_studio_credits(oleg.id, 100)             # купил 100 кредитов на новом сервере
    db.create_payment(oleg.id, 199, "gp-1", plan="credits:100")
    db.set_prefs(oleg.id, {"playlists": [{"id": "p-new", "name": "Новый", "items": []}], "now": {"q": []}})
    teacher = db.ensure_user(None)
    db.register(teacher.id, "teacher@school.ru", auth.hash_password("пароль-учителя-1"))
    db.give_welcome(teacher.id, 30)
    song = db.create_job(teacher.id, "Гимн класса.mp3", {"kind": "studio", "mode": "create"})
    (folder / "studio" / song.id).mkdir(parents=True)
    (folder / "studio" / song.id / "v1.mp3").write_bytes(b"hymn")
    anon = db.ensure_user(None)
    db.create_job(anon.id, "аноним.mp3", {"kind": "studio", "mode": "stems"})
    yx = db.ensure_user(None)
    db.register(yx.id, "yandex-777@yandex.id", "oauth$y")
    db.link_provider(yx.id, "yandex", "777")
    (folder / "genapi_key").write_text("НОВЫЙ-КЛЮЧ")
    (folder / "voices" / oleg.id).mkdir(parents=True)
    (folder / "voices" / oleg.id / "state.json").write_text('{"stage": "ready"}')
    ids.update(owner=owner.id, oleg=oleg.id, teacher=teacher.id, anon=anon.id, song=song.id, yx=yx.id)
    return ids


def run(tmp, *args):
    env = {**os.environ, "NASLUX_DATA": str(tmp / "srv" / "data"), "NASLUX_APP": str(ROOT),
           "NASLUX_PY": sys.executable, "NASLUX_SYSTEMCTL": "true", "NASLUX_CHOWN": "true",
           "NASLUX_WORK": str(tmp), "NASLUX_WAIT": "0"}
    return subprocess.run(["bash", str(ROOT / "deploy/restore_data.sh"), *args],
                          capture_output=True, text=True, env=env)


def test_backup_is_merged_with_what_appeared_after_the_move(tmp_path):
    backup_root = tmp_path / "beget" / "opt" / "nasluh" / "data"
    backup_root.mkdir(parents=True)
    old = build_old(backup_root)
    live = tmp_path / "srv" / "data"
    live.mkdir(parents=True)
    new = build_new(live)
    archive = tmp_path / "backup_naslux.tar"
    with tarfile.open(archive, "w") as tar:                  # как у Beget: весь путь от корня
        tar.add(tmp_path / "beget" / "opt", arcname="opt")
    before = (live / "app.db").read_bytes()

    plan = run(tmp_path, str(archive))
    assert plan.returncode == 0, plan.stderr + plan.stdout
    assert "ПЛАН" in plan.stdout and "Новые люди" in plan.stdout
    assert (live / "app.db").read_bytes() == before                         # план ничего не трогает
    assert not any(p.name.startswith("app.db-") for p in live.iterdir())    # и служебных файлов не плодит

    done = run(tmp_path, str(archive), "--apply")
    assert done.returncode == 0, done.stderr + done.stdout
    db = Storage(str(live / "app.db"))

    def pw(user_id):
        import sqlite3
        with sqlite3.connect(live / "app.db") as conn:
            return conn.execute("SELECT password_hash FROM users WHERE id = ?", (user_id,)).fetchone()[0]

    owner = db.user_by_email("owner@example.com")
    assert owner.id == old["owner"] and owner.unlimited                     # старый аккаунт
    assert auth.verify_password("новый-пароль-владельца", pw(owner.id))
    oleg = db.user(old["oleg"])
    assert oleg.balance == 50                     # старый остаток; возмещение 209 не задвоено, траты не вычтены
    assert oleg.studio_credits == 40 + 100        # купленное на новом -- прибавилось
    assert auth.verify_password("новый-пароль-олега", pw(oleg.id))
    lists = [p["id"] for p in db.prefs(oleg.id)["playlists"]]
    assert lists == ["p-old", "p-new"]
    assert db.user(old["ivan"]) is not None                                  # был только в бэкапе
    teacher = db.user_by_email("teacher@school.ru")
    assert teacher.id == new["teacher"] and teacher.studio_credits == 30     # новый человек целиком
    song = db.job(new["song"])
    assert song.user_id == new["teacher"]
    assert (live / "studio" / new["song"] / "v1.mp3").read_bytes() == b"hymn"
    assert (live / "studio" / old["old_job"] / "v1.mp3").read_bytes() == b"old"
    assert db.user_by_provider("yandex", "777").id == old["yx"]              # вход через Яндекс -- старый аккаунт
    assert db.user(new["anon"]) is not None
    assert [p["amount"] for p in db.user_payments(oleg.id)] == [199]       # оплата с нового сервера -- у старого аккаунта
    assert (live / "genapi_key").read_text() == "НОВЫЙ-КЛЮЧ"                 # утёкший ключ не вернулся
    assert (live / "voices" / old["oleg"] / "state.json").exists()
    assert not (live / "models" / "big.bin").exists()                         # модели из бэкапа не тащим
    backups = [p for p in live.parent.iterdir() if p.name.startswith("data.before-restore-")]
    assert len(backups) == 1 and (backups[0] / "app.db").read_bytes() == before   # откат возможен


def test_probe_finds_data_folder_and_size_without_unpacking(tmp_path):
    """Бэкап -- снимок всего сервера (19 ГБ), диска меньше: папку данных и её
    размер узнаём по заголовкам, модели в размер не входят."""
    root = tmp_path / "snap"
    data = root / "opt" / "nasluh" / "data"
    (data / "models").mkdir(parents=True)
    (data / "app.db").write_bytes(b"x" * 4096)
    (data / "studio").mkdir()
    (data / "studio" / "a.mp3").write_bytes(b"y" * 8192)
    (data / "models" / "whisper.bin").write_bytes(b"z" * 500_000)
    (root / "var" / "www").mkdir(parents=True)
    (root / "var" / "www" / "app.db").write_bytes(b"other")          # чужая база -- не наша
    archive = tmp_path / "snap.tar"
    with tarfile.open(archive, "w") as tar:
        tar.add(root, arcname=".")
    out = subprocess.run([sys.executable, "-I", str(ROOT / "deploy/backup_probe.py"), str(archive)],
                         capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    folder, kb = out.stdout.split()
    assert folder == "opt/nasluh/data"
    assert 12 <= int(kb) < 100                                          # без 500 КБ моделей
