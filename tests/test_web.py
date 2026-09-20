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
