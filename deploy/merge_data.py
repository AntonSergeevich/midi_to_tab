"""
Объединить данные NASLUX: база из бэкапа старого сервера + всё, что появилось
на новом после переезда (владелец, 18.11: «скачал архив бэкапа с Beget,
давай ставить»).

    python3 deploy/merge_data.py --into /opt/nasluh/data --from /opt/nasluh/data.before-restore-XXXX [--dry]

--into -- папка данных из бэкапа (старая база, ОСНОВА; в неё и пишем).
--from -- папка данных нового сервера (её только читаем).

Правила:
- Новые люди (их нет в старой базе ни по почте, ни по входу Яндекс/VK) --
  целиком, с треками, оплатами, плейлистами и файлами.
- Человек есть в обеих базах (одна почта или один вход Яндекс/VK) -- это
  один аккаунт со СТАРЫМ id: остаётся новый пароль (после взлома просили
  сменить), новые треки и плейлисты добавляются к старым, а деньги и
  кредиты -- старые плюс то, что прибавилось на новом сервере сверх
  стартового (возмещение при переносе из GetPlatinum и приветственные
  кредиты повторно не начисляются; потраченное на новом сервере старый
  баланс не уменьшает).
- Ключи (genapi_key, beget_ai_key) и служебное состояние -- с нового
  сервера: старые после взлома считаются утёкшими.
Повторный запуск ничего не задваивает (INSERT OR IGNORE, уже перенесённые
треки пропускаются).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from web.storage import Storage  # noqa: E402

MIGRATED = "перенос:getplatinum"
WELCOME = 30
JOB_DIRS = ("studio", "uploads", "results")
FRESH_FILES = ("genapi_key", "beget_ai_key", "runpod_endpoints.json", "mureka_broke.json")
# таблицы, где строка принадлежит человеку (user_id) -- переносим с заменой id
# (дневные счётчики job_counts/studio_upload_counts не переносим: у них нет
# ключа -- повторный запуск задвоил бы строки, а потерять их не страшно)
OWNED = ("payments", "tickets", "voices", "playlist_shares", "shares", "gifts", "gift_takes")


def connect(path: str, readonly: bool = False) -> sqlite3.Connection:
    # базу нового сервера только читаем: даже открыть её на запись от root
    # значило бы рискнуть служебными файлами SQLite, чужими для сайта
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30) if readonly \
        else sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def columns(conn, table: str) -> list[str]:
    return [r["name"] for r in conn.execute(f"PRAGMA table_info({table})")]


def has_table(conn, table: str) -> bool:
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone())


def norm(email) -> str:
    return (email or "").strip().lower()


def real_password(value) -> bool:
    return bool(value) and not str(value).startswith("oauth$")


def start_values(user: sqlite3.Row) -> dict:
    """С чего человек начал на новом сервере: перенесённым из GetPlatinum --
    возмещённая сумма на балансе, остальным -- приветственные кредиты."""
    note = user["note"] or ""
    paid = re.search(r"оплачено (\d+(?:[.,]\d+)?) ₽", note)
    migrated = MIGRATED in note
    return {"balance": float(paid.group(1).replace(",", ".")) if migrated and paid else 0.0,
            "studio_credits": 0 if migrated else (WELCOME if user["welcome_given"] else 0),
            "credits": 0}


def merge_prefs(old_raw, new_raw) -> str | None:
    def load(raw):
        try:
            data = json.loads(raw) if raw else {}
            return data if isinstance(data, dict) else {}
        except ValueError:
            return {}
    old, new = load(old_raw), load(new_raw)
    if not new:
        return old_raw
    out = {**old, **new}
    lists = {p.get("id"): p for p in (old.get("playlists") or []) if isinstance(p, dict)}
    for p in new.get("playlists") or []:
        if isinstance(p, dict):
            lists.setdefault(p.get("id"), p)
    out["playlists"] = list(lists.values())
    return json.dumps(out, ensure_ascii=False)


def _link_or_copy(src, dst) -> None:
    try:
        os.link(src, dst)              # тот же диск: файл не занимает места второй раз
    except OSError:
        shutil.copy2(src, dst)


def place(item: Path, target: Path) -> None:
    """Файл или папку трека -- в новую папку данных жёсткими ссылками
    (на диске сервера мало места: 19.11 свободно ~8 ГБ при бэкапе в 19)."""
    if item.is_dir():
        shutil.copytree(item, target, copy_function=_link_or_copy)
    else:
        _link_or_copy(item, target)


def plan_users(old, new) -> tuple[dict, list, list]:
    """(new_id -> old_id для совпавших, совпавшие [(new, old)], новые [new])."""
    by_email, by_yandex, by_vk = {}, {}, {}
    for row in old.execute("SELECT * FROM users"):
        if norm(row["email"]):
            by_email.setdefault(norm(row["email"]), row)
        if row["yandex_id"]:
            by_yandex.setdefault(row["yandex_id"], row)
        if row["vk_id"]:
            by_vk.setdefault(row["vk_id"], row)
    mapping, same, fresh = {}, [], []
    old_ids = {r["id"] for r in old.execute("SELECT id FROM users")}
    for row in new.execute("SELECT * FROM users"):
        twin = (by_email.get(norm(row["email"])) if norm(row["email"]) else None) \
            or (by_yandex.get(row["yandex_id"]) if row["yandex_id"] else None) \
            or (by_vk.get(row["vk_id"]) if row["vk_id"] else None)
        if twin is None and row["id"] in old_ids:
            twin = old.execute("SELECT * FROM users WHERE id = ?", (row["id"],)).fetchone()
        if twin is not None:
            mapping[row["id"]] = twin["id"]
            same.append((row, twin))
        else:
            fresh.append(row)
    return mapping, same, fresh


def merge(into: str, frm: str, dry: bool = False, say=print) -> dict:
    into_db, from_db = os.path.join(into, "app.db"), os.path.join(frm, "app.db")
    if not os.path.isfile(into_db) or not os.path.isfile(from_db):
        raise SystemExit(f"Нет базы: {into_db if not os.path.isfile(into_db) else from_db}")
    Storage(into_db)          # старая база -- до нынешних колонок
    old, new = connect(into_db), connect(from_db, readonly=True)
    for name, conn in (("старая", old), ("новая", new)):
        check = conn.execute("PRAGMA integrity_check").fetchone()[0]
        if check != "ok":
            raise SystemExit(f"{name} база повреждена: {check}")
    mapping, same, fresh = plan_users(old, new)
    remap = lambda uid: mapping.get(uid, uid)  # noqa: E731
    old_jobs = {r["id"] for r in old.execute("SELECT id FROM jobs")}
    new_jobs = [r for r in new.execute("SELECT * FROM jobs") if r["id"] not in old_jobs]
    report = {"совпали": len(same), "новые_люди": len(fresh), "новые_треки": len(new_jobs)}

    say(f"Бэкап: людей {old.execute('SELECT COUNT(*) FROM users').fetchone()[0]}, треков {len(old_jobs)}. "
        f"Новый сервер: людей {new.execute('SELECT COUNT(*) FROM users').fetchone()[0]}, "
        f"треков {new.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]}.")
    say(f"Один и тот же человек в обеих базах: {len(same)}")
    for n, o in same:
        if not (n["email"] or n["yandex_id"] or n["vk_id"]):
            continue
        add = {k: max(0.0, float(n[k] or 0) - v) for k, v in start_values(n).items()}
        extra = ", ".join(f"+{add[k]:g} {label}" for k, label in
                          (("studio_credits", "кредитов"), ("balance", "₽"), ("credits", "разборов")) if add[k]) or "без доплат"
        say(f"  {n['email'] or n['yandex_id'] or n['vk_id']}: остаётся старый аккаунт, {extra}"
            f"{', новый пароль' if real_password(n['password_hash']) else ''}")
    say(f"Новые люди (только на новом сервере): {len(fresh)}, из них с почтой: "
        f"{sum(1 for r in fresh if r['email'])}")
    say(f"Треков с нового сервера: {len(new_jobs)}")
    if dry:
        say("ПЛАН -- ничего не изменено.")
        return report

    user_cols = columns(old, "users")
    with old:
        for row in fresh:
            keep = [c for c in row.keys() if c in user_cols]
            old.execute(f"INSERT OR IGNORE INTO users ({','.join(keep)}) VALUES ({','.join('?' * len(keep))})",
                        [row[c] for c in keep])
        for n, o in same:
            add = {k: max(0.0, float(n[k] or 0) - v) for k, v in start_values(n).items()}
            sets = {"studio_credits": int((o["studio_credits"] or 0) + add["studio_credits"]),
                    "balance": round(float(o["balance"] or 0) + add["balance"], 2),
                    "credits": int((o["credits"] or 0) + add["credits"]),
                    "is_admin": int(bool(o["is_admin"] or n["is_admin"])),
                    "unlimited": int(bool(o["unlimited"] or n["unlimited"])),
                    "prefs": merge_prefs(o["prefs"], n["prefs"])}
            if (n["paid_until"] or 0) > (o["paid_until"] or 0):
                sets.update(paid_until=n["paid_until"], tier=n["tier"] or o["tier"])
            if real_password(n["password_hash"]):
                sets["password_hash"] = n["password_hash"]
            for oauth in ("yandex_id", "vk_id"):
                if n[oauth] and not o[oauth]:
                    sets[oauth] = n[oauth]
            if not o["email"] and n["email"]:
                sets["email"] = n["email"]
            old.execute(f"UPDATE users SET {', '.join(f'{k} = ?' for k in sets)} WHERE id = ?",
                        [*sets.values(), o["id"]])
        job_cols = columns(old, "jobs")
        for row in new_jobs:
            keep = [c for c in row.keys() if c in job_cols]
            values = [remap(row[c]) if c == "user_id" else row[c] for c in keep]
            old.execute(f"INSERT OR IGNORE INTO jobs ({','.join(keep)}) VALUES ({','.join('?' * len(keep))})", values)
        for table in OWNED:
            if not (has_table(new, table) and has_table(old, table)):
                continue
            cols = [c for c in columns(new, table) if c in columns(old, table)]
            for row in new.execute(f"SELECT * FROM {table}"):
                values = [remap(row[c]) if c == "user_id" else row[c] for c in cols]
                old.execute(f"INSERT OR IGNORE INTO {table} ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                            values)

    # файлы треков с нового сервера
    copied = 0
    for row in new_jobs:
        for sub in JOB_DIRS:
            folder = Path(frm, sub)
            if not folder.is_dir():
                continue
            for item in folder.glob(f"{row['id']}*"):
                target = Path(into, sub, item.name)
                if target.exists():
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                place(item, target)
                copied += 1
    # свой голос -- папка человека, под старым id
    voices = Path(frm, "voices")
    if voices.is_dir():
        for item in voices.iterdir():
            target = Path(into, "voices", remap(item.name))
            if item.is_dir() and not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                place(item, target)
    # ключи и служебное -- с нового сервера; старых ключей не оставляем
    for name in FRESH_FILES:
        src, dst = Path(frm, name), Path(into, name)
        if src.is_file():
            shutil.copy2(src, dst)
        elif dst.is_file() and name.endswith("_key"):
            dst.unlink()
    tags_old, tags_new = Path(into, "style_tags.json"), Path(frm, "style_tags.json")
    if tags_new.is_file():
        try:
            merged = {**json.loads(tags_old.read_text(encoding="utf-8") if tags_old.is_file() else "{}"),
                      **json.loads(tags_new.read_text(encoding="utf-8"))}
            tags_old.write_text(json.dumps(merged, ensure_ascii=False), encoding="utf-8")
        except ValueError:
            pass
    report["файлов"] = copied
    say(f"Готово: добавлено людей {len(fresh)}, объединено {len(same)}, треков {len(new_jobs)}, "
        f"папок и файлов треков {copied}.")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--into", required=True, help="папка данных из бэкапа (основа)")
    parser.add_argument("--from", dest="frm", required=True, help="папка данных нового сервера")
    parser.add_argument("--dry", action="store_true", help="только показать план")
    args = parser.parse_args()
    merge(args.into, args.frm, args.dry)


if __name__ == "__main__":
    main()
