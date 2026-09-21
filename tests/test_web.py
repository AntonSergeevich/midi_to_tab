"""Тесты веб-слоя: аккорды, квоты, хранилище, безопасность имён."""

from __future__ import annotations

import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from midi2tab.chords import detect, match, name_of
from midi2tab.midiin import NoteEvent
from midi2tab.timing import TPQ


def chord_notes(pitches, start, length=TPQ * 4):
    return [NoteEvent(start, length, p, 90) for p in pitches]


# ------------------------------------------------------------------ аккорды


@pytest.mark.parametrize(
    "name,pitches",
    [
        ("C", [48, 52, 55, 60]),
        ("Am", [45, 52, 57, 60]),
        ("G", [43, 47, 50, 55]),
        ("Dm7", [50, 53, 57, 60]),
        ("G7", [43, 47, 53, 55]),
        ("Cmaj7", [48, 52, 55, 59]),
        ("Em", [40, 47, 52, 55]),
        ("A7", [45, 49, 52, 55]),
    ],
)
def test_common_chords_are_named_correctly(name, pitches):
    spans = detect(chord_notes(pitches, 0))
    assert spans, f"аккорд {name} вообще не распознан"
    assert spans[0].name == name


def test_slash_chord_uses_bass_note():
    """Аккорд с басом не в основном тоне пишется через дробь."""
    spans = detect(chord_notes([43, 52, 55, 60], 0))  # C с соль в басу
    assert spans[0].name == "C/G"


def test_same_notes_named_by_bass():
    """
    Ля-ре-ми это одновременно Asus4 и Dsus2.
    Решает бас: снизу ля -- значит Asus4.
    """
    spans = detect(chord_notes([45, 50, 52, 57], 0))
    assert spans[0].name == "Asus4"


def test_progression_keeps_order_and_timing():
    notes = []
    # у каждого аккорда должна быть терция, иначе это квинт-аккорд:
    # ля-ми-ля без до -- это A5, а не Am, и распознаётся верно именно так
    for i, pitches in enumerate([[48, 52, 55], [45, 52, 57, 60], [41, 45, 48], [43, 47, 50]]):
        notes += chord_notes(pitches, i * TPQ * 4)
    spans = detect(notes)
    assert [s.name for s in spans] == ["C", "Am", "F", "G"]
    assert spans[0].start == 0
    assert spans[1].start == TPQ * 4
    for span in spans:
        start, end = span.seconds(120)
        assert end > start


def test_silence_produces_no_chord():
    assert detect([]) == []
    quality, root, confidence = match([0.0] * 12)
    assert root == -1 and confidence == 0.0


def test_name_of_handles_bass_equal_to_root():
    assert name_of(0, "", 0) == "C"
    assert name_of(0, "m", 7) == "Cm/G"


# ------------------------------------------------------- квоты и подписка


@pytest.fixture()
def store(tmp_path):
    from web.storage import Storage

    return Storage(str(tmp_path / "test.db"))


def test_two_free_songs_then_blocked(store):
    from web import billing

    user = store.ensure_user(None)
    for _ in range(billing.FREE_SONGS):
        assert billing.check_access(user).allowed
        billing.consume(store, user)
        user = store.user(user.id)
    blocked = billing.check_access(user)
    assert not blocked.allowed
    assert "199" in blocked.reason


def test_subscription_unblocks_and_does_not_spend_free(store):
    from web import billing

    user = store.ensure_user(None)
    for _ in range(billing.FREE_SONGS):
        billing.consume(store, user)
        user = store.user(user.id)
    billing.grant_subscription(store, user.id)
    user = store.user(user.id)

    assert billing.check_access(user).allowed
    before = user.free_used
    billing.consume(store, user)          # у подписчика не списывается
    assert store.user(user.id).free_used == before


def test_renewal_adds_days_instead_of_resetting(store):
    from web import billing

    user = store.ensure_user(None)
    first = billing.grant_subscription(store, user.id, days=30)
    second = billing.grant_subscription(store, user.id, days=30)
    assert second - first == pytest.approx(30 * 86400, abs=5)


def test_expired_subscription_is_not_active(store):
    user = store.ensure_user(None)
    store.extend_subscription(user.id, time.time() - 10)
    assert not store.user(user.id).subscribed


def test_payment_provider_refuses_without_credentials(monkeypatch):
    from web import billing

    monkeypatch.delenv("YOOKASSA_SHOP_ID", raising=False)
    monkeypatch.delenv("YOOKASSA_SECRET_KEY", raising=False)
    gateway = billing.YooKassaProvider()
    assert not gateway.configured()
    with pytest.raises(RuntimeError, match="не настроен"):
        gateway.create_payment("u1", 199.0, "https://example.com")


def test_webhook_payload_parsing():
    from web import billing

    gateway = billing.YooKassaProvider()
    assert gateway.verify_webhook({"object": {"id": "p1", "status": "succeeded"}}) == (
        "p1",
        "succeeded",
    )
    assert gateway.verify_webhook({"object": {}}) is None


# --------------------------------------------------------------- хранилище


def test_jobs_roundtrip(store):
    user = store.ensure_user(None)
    job = store.create_job(user.id, "song.mp3", {"tuning": "std"})
    store.update_job(job.id, status="running", stage="Работаю")
    assert store.job(job.id).stage == "Работаю"
    store.update_job(job.id, status="done", result={"player": {"chords": []}})
    done = store.job(job.id)
    assert done.status == "done"
    assert done.result["player"] == {"chords": []}
    assert [j.id for j in store.user_jobs(user.id)] == [job.id]


def test_user_id_is_stable(store):
    first = store.ensure_user(None)
    again = store.ensure_user(first.id)
    assert again.id == first.id


# ---------------------------------------------------------- имена файлов


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Пожары_D_minor125 (Guitar).wav", "Пожары_D_minor125 (Guitar)"),
        ("../../etc/passwd", "passwd"),
        ("", "song"),
        ("....", "song"),
    ],
)
def test_upload_name_is_sanitised(raw, expected, monkeypatch, tmp_path):
    """В имени может прийти обход каталога -- его обязано срезать."""
    monkeypatch.setenv("MIDI2TAB_DATA", str(tmp_path))
    monkeypatch.setenv("MIDI2TAB_SECRET", "test")
    from web.app import safe_stem

    result = safe_stem(raw)
    assert result == expected
    assert "/" not in result and "\\" not in result and ".." not in result


def test_power_chord_without_third_is_named_five():
    """Без терции аккорд не мажор и не минор -- это квинт-аккорд."""
    spans = detect(chord_notes([45, 52, 57], 0))
    assert spans[0].name == "A5"


# ------------------------------------------------------------------ админка


def test_unlimited_beats_all_limits(store):
    """Безлимит выдаётся вручную и обязан бить любые счётчики."""
    from web import billing

    user = store.ensure_user(None)
    for _ in range(billing.FREE_SONGS):
        billing.consume(store, user)
        user = store.user(user.id)
    assert not billing.check_access(user).allowed

    store.set_flags(user.id, unlimited=True)
    user = store.user(user.id)
    access = billing.check_access(user)
    assert access.allowed
    assert "Безлимит" in access.reason


def test_unlimited_does_not_spend_free(store):
    from web import billing

    user = store.ensure_user(None)
    store.set_flags(user.id, unlimited=True)
    user = store.user(user.id)
    billing.consume(store, user)
    assert store.user(user.id).free_used == 0


def test_admin_flags_and_note(store):
    user = store.ensure_user(None)
    store.set_flags(user.id, is_admin=True, note="тестировщик")
    fresh = store.user(user.id)
    assert fresh.is_admin and fresh.note == "тестировщик"


def test_migration_keeps_existing_users(tmp_path):
    """
    Регрессия: колонки добавлены позже, а база у работающего сервиса
    уже содержит пользователей -- пересоздавать её нельзя.
    """
    import sqlite3

    from web.storage import Storage

    path = str(tmp_path / "old.db")
    conn = sqlite3.connect(path)
    conn.executescript(
        "CREATE TABLE users (id TEXT PRIMARY KEY, created_at REAL NOT NULL,"
        " free_used INTEGER NOT NULL DEFAULT 0, paid_until REAL, email TEXT);"
        "CREATE TABLE jobs (id TEXT PRIMARY KEY, user_id TEXT, created_at REAL,"
        " filename TEXT, status TEXT, stage TEXT DEFAULT '', error TEXT,"
        " settings TEXT DEFAULT '{}', result TEXT, counted INTEGER DEFAULT 0);"
        "CREATE TABLE payments (id TEXT PRIMARY KEY, user_id TEXT, created_at REAL,"
        " amount REAL, status TEXT, provider_id TEXT);"
    )
    conn.execute("INSERT INTO users VALUES ('keep', 1000.0, 2, NULL, NULL)")
    conn.commit()
    conn.close()

    store = Storage(path)
    user = store.user("keep")
    assert user is not None
    assert user.free_used == 2          # данные уцелели
    assert user.unlimited is False      # новая колонка появилась


def test_stats_counts_everything(store):
    from web import billing

    owner = store.ensure_user(None)
    store.set_flags(owner.id, unlimited=True, is_admin=True)
    other = store.ensure_user("second")
    billing.grant_subscription(store, other.id)
    store.create_job(owner.id, "a.mp3", {})

    stats = store.stats()
    assert stats["users"] == 2
    assert stats["unlimited"] == 1
    assert stats["paid"] == 1
    assert stats["jobs"] == 1


def test_payment_provider_selection(monkeypatch):
    from web import billing

    monkeypatch.setenv("PAYMENT_PROVIDER", "getplatinum")
    assert billing.provider().name == "getplatinum"
    monkeypatch.setenv("PAYMENT_PROVIDER", "yookassa")
    assert billing.provider().name == "yookassa"


def test_getplatinum_normalises_success_statuses(monkeypatch):
    from web import billing

    monkeypatch.setenv("GETPLATINUM_SECRET_KEY", "тайна")
    gateway = billing.GetPlatinumProvider()

    def notice(status):
        body = {"payment_id": "x", "order_id": "x", "terminal": gateway.terminal,
                "amount": "199.00", "status": status}
        body["signature"] = gateway.sign(body, gateway.CALLBACK_SIGN_FIELDS)
        return body

    for raw in ("paid", "success", "succeeded", "completed", "confirmed"):
        assert gateway.verify_webhook(notice(raw))[1] == "succeeded"
    assert gateway.verify_webhook(notice("canceled"))[1] == "canceled"
    assert gateway.verify_webhook({}) is None


def test_getplatinum_refuses_unsigned_notice(monkeypatch):
    """
    Уведомление без верной подписи -- не уведомление.

    Адрес обработчика не секрет: он прописан в кабинете мерчанта и
    виден в логах. Если верить телу запроса на слово, подписку себе
    выпишет любой, кто отправит туда {"status": "paid"}.
    """
    from web import billing

    monkeypatch.setenv("GETPLATINUM_SECRET_KEY", "тайна")
    gateway = billing.GetPlatinumProvider()
    body = {"payment_id": "x", "order_id": "x", "terminal": gateway.terminal,
            "amount": "199.00", "status": "paid"}

    assert gateway.verify_webhook(body) is None                       # без подписи
    assert gateway.verify_webhook({**body, "signature": "0" * 64}) is None   # чужая

    body["signature"] = gateway.sign(body, gateway.CALLBACK_SIGN_FIELDS)
    assert gateway.verify_webhook(body) == ("x", "succeeded")

    # Подменённая сумма ломает подпись -- значит, и сумму подделать нельзя
    assert gateway.verify_webhook({**body, "amount": "1.00"}) is None


def test_getplatinum_terminal_is_ours_by_default():
    from web import billing

    assert billing.GetPlatinumProvider().terminal == "153777"


# -------------------------------------------------------------- два тарифа


def test_single_track_purchase(store):
    """Разовая покупка даёт ровно один трек."""
    from web import billing

    user = store.ensure_user(None)
    for _ in range(billing.FREE_SONGS):
        billing.consume(store, user)
        user = store.user(user.id)
    assert not billing.check_access(user).allowed

    billing.apply_plan(store, user.id, "single")
    user = store.user(user.id)
    assert billing.check_access(user).allowed
    billing.consume(store, user)
    user = store.user(user.id)
    assert not billing.check_access(user).allowed


def test_free_songs_spent_before_paid_credits(store):
    """Купленный трек не должен сгорать раньше бесплатного."""
    from web import billing

    user = store.ensure_user(None)
    billing.apply_plan(store, user.id, "single")
    user = store.user(user.id)

    billing.consume(store, user)
    user = store.user(user.id)
    assert user.credits == 1           # потратили пробную, не купленную
    assert user.free_used == 1


def test_subscription_plan_grants_days_not_credits(store):
    from web import billing

    user = store.ensure_user(None)
    billing.apply_plan(store, user.id, "month")
    user = store.user(user.id)
    assert user.subscribed
    assert user.credits == 0


def test_payment_remembers_plan(store):
    user = store.ensure_user(None)
    store.create_payment(user.id, 19.0, "prov-1", plan="single")
    record = store.payment_by_provider("prov-1")
    assert record["plan"] == "single"
    assert record["amount"] == 19.0


# ------------------------------------------------------ независимость сервера


def test_web_does_not_need_desktop_playback():
    """
    Регрессия: pygame лежал в основных зависимостях и ломал развёртывание.

    На Ubuntu 26.04 с Python 3.14 готовой сборки у него нет, pip пытался
    собрать из исходников и падал на отсутствии заголовков SDL. Серверу
    он не нужен вовсе: в вебе звук синтезируется в браузере.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    core = (root / "requirements.txt").read_text(encoding="utf-8").lower()
    assert "pygame" not in core.replace("requirements-desktop", "")

    # и ни один модуль веб-слоя не должен его тянуть
    for module in (root / "web").glob("*.py"):
        text = module.read_text(encoding="utf-8")
        assert "import pygame" not in text
        assert "from midi2tab import playback" not in text
        assert "playback" not in text.replace("# ", "")


def test_desktop_requirements_exist():
    from pathlib import Path

    desktop = Path(__file__).resolve().parent.parent / "requirements-desktop.txt"
    assert desktop.is_file()
    assert "pygame" in desktop.read_text(encoding="utf-8").lower()
