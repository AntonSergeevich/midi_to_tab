"""Перенос клиентов из выгрузки GetPlatinum после переезда (deploy/admin.py перенос)."""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CSV = """ФИО;E-mail;Телефон;Дата создания;Сумма покупок
;vk-1@vk.id;;2026-10-21 23:17;0 RUB
Олег;oleg@example.com;+79110000001;2026-10-17 00:53;209.00 RUB
Иванова Анна;anna@example.com;+79230000002;2026-10-10 18:34;99.00 RUB
Владелец;owner@example.com;+79950000003;2026-09-22 18:29;69.00 RUB
Проверка оплаты;owner@example.com;+79950000003;2026-08-04 15:44;10.00 RUB
"""


def run(tmp, *args):
    env = {**os.environ, "MIDI2TAB_DATA": str(tmp)}
    return subprocess.run([sys.executable, str(ROOT / "deploy/admin.py"), *args],
                          capture_output=True, text=True, env=env, cwd=ROOT)


def test_migrate_plans_then_applies_once(tmp_path):
    sys.path.insert(0, str(ROOT))
    from web.storage import Storage

    db = Storage(str(tmp_path / "app.db"))
    table = tmp_path / "clients.csv"
    table.write_text(CSV, encoding="utf-8")
    plan = run(tmp_path, "перенос", str(table), "--skip", "owner@example.com")
    assert plan.returncode == 0, plan.stderr
    assert "ПЛАН" in plan.stdout and "+209 ₽" in plan.stdout and "→ пропуск" in plan.stdout
    assert db.user_by_email("oleg@example.com") is None              # без --apply ничего не меняется

    done = run(tmp_path, "перенос", str(table), "--apply", "--skip", "owner@example.com")
    assert done.returncode == 0, done.stderr
    oleg = db.user_by_email("oleg@example.com")
    assert oleg and oleg.balance == 209 and "+79110000001" in oleg.note and "перенос:getplatinum" in oleg.note
    assert db.user_by_email("anna@example.com").balance == 99
    assert db.user_by_email("vk-1@vk.id") is not None
    assert db.user_by_email("owner@example.com") is None

    again = run(tmp_path, "перенос", str(table), "--apply", "--skip", "owner@example.com")
    assert "уже есть" in again.stdout
    assert db.user_by_email("oleg@example.com").balance == 209      # дважды не начисляет


def test_migrate_sums_repeat_purchases(tmp_path):
    sys.path.insert(0, str(ROOT))
    from deploy import admin  # noqa: F401  (проверяем, что модуль грузится)
    table = tmp_path / "c.csv"
    table.write_text(CSV, encoding="utf-8")
    clients = {c["email"]: c for c in admin.read_clients(str(table))}
    assert clients["owner@example.com"]["paid"] == 79 and clients["owner@example.com"]["name"] == "Владелец"
