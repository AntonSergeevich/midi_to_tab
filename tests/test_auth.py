"""Учётные записи, обращения и удаление. Самое чувствительное место."""

from __future__ import annotations

import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from web import auth, support


@pytest.fixture()
def store(tmp_path):
    from web.storage import Storage

    return Storage(str(tmp_path / "auth.db"))


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """
    Живое приложение на временной базе.

    Модуль web.app создаёт хранилище на импорте, поэтому папку данных
    подменяем ДО него и выгружаем из кеша -- иначе тесты писали бы в
    настоящую базу сервиса.
    """
    import sys

    monkeypatch.setenv("MIDI2TAB_DATA", str(tmp_path / "data"))
    for name in [m for m in sys.modules if m.startswith("web.")]:
        del sys.modules[name]
    from fastapi.testclient import TestClient

    import web.app as app_module

    with TestClient(app_module.app) as test_client:
        yield test_client


# ------------------------------------------------------------------ пароли


def test_password_roundtrip():
    stored = auth.hash_password("надёжный-пароль-42")
    assert auth.verify_password("надёжный-пароль-42", stored)
    assert not auth.verify_password("надёжный-пароль-43", stored)


def test_hash_is_salted():
    """Два одинаковых пароля обязаны дать разные хеши."""
    first = auth.hash_password("один-и-тот-же-пароль")
    second = auth.hash_password("один-и-тот-же-пароль")
    assert first != second
    assert auth.verify_password("один-и-тот-же-пароль", first)
    assert auth.verify_password("один-и-тот-же-пароль", second)


def test_password_never_stored_in_clear():
    password = "секретное-слово-99"
    stored = auth.hash_password(password)
    assert password not in stored
    assert stored.startswith("scrypt$")


@pytest.mark.parametrize("bad", ["", "короче", "   ", "x" * 300])
def test_weak_passwords_rejected(bad):
    with pytest.raises(auth.AuthError):
        auth.hash_password(bad)


def test_verify_survives_broken_hash():
    """Испорченная запись в базе не должна ронять вход."""
    for broken in (None, "", "мусор", "scrypt$не$числа$x$y$z"):
        assert auth.verify_password("любой", broken) is False


# ------------------------------------------------------------------ почта


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Ivan@Mail.RU", "ivan@mail.ru"),
        ("  user@example.com  ", "user@example.com"),
    ],
)
def test_email_normalised(raw, expected):
    assert auth.normalise_email(raw) == expected


@pytest.mark.parametrize("bad", ["нет-собаки", "a@b", "@mail.ru", "a@b.c", 123])
def test_bad_emails_rejected(bad):
    with pytest.raises(auth.AuthError):
        auth.normalise_email(bad)


# ------------------------------------------------------- попытки подбора


def test_limiter_blocks_after_attempts():
    limiter = auth.AttemptLimiter(max_attempts=3, window=60)
    for _ in range(3):
        assert not limiter.blocked("ivan@mail.ru")
        limiter.note_failure("ivan@mail.ru")
    assert limiter.blocked("ivan@mail.ru")
    limiter.reset("ivan@mail.ru")
    assert not limiter.blocked("ivan@mail.ru")


def test_limiter_forgets_old_attempts():
    limiter = auth.AttemptLimiter(max_attempts=2, window=0)
    limiter.note_failure("x")
    limiter.note_failure("x")
    assert not limiter.blocked("x")   # окно истекло


def test_limiter_is_per_account():
    limiter = auth.AttemptLimiter(max_attempts=2, window=60)
    limiter.note_failure("a@mail.ru")
    limiter.note_failure("a@mail.ru")
    assert limiter.blocked("a@mail.ru")
    assert not limiter.blocked("b@mail.ru")


# ------------------------------------------------------------ регистрация


def test_registration_keeps_anonymous_tracks(store):
    """Разобранное до регистрации обязано остаться при человеке."""
    visitor = store.ensure_user(None)
    store.create_job(visitor.id, "Пожары.mp3", {})

    store.register(visitor.id, "ivan@mail.ru", auth.hash_password("пароль-12345"))
    account = store.user(visitor.id)

    assert account.registered
    assert account.email == "ivan@mail.ru"
    assert len(store.root_jobs(account.id)) == 1


def test_duplicate_email_rejected(store):
    first = store.ensure_user(None)
    store.register(first.id, "ivan@mail.ru", auth.hash_password("пароль-12345"))

    second = store.ensure_user(None)
    with pytest.raises(ValueError):
        store.register(second.id, "ivan@mail.ru", auth.hash_password("другой-пароль"))


def test_login_moves_anonymous_tracks(store):
    """Вошёл в старый аккаунт -- свежеразобранное переезжает к нему."""
    account = store.ensure_user(None)
    store.register(account.id, "ivan@mail.ru", auth.hash_password("пароль-12345"))

    visitor = store.ensure_user(None)
    store.create_job(visitor.id, "Новая.mp3", {})

    moved = store.move_jobs(visitor.id, account.id)
    store.delete_user_if_empty(visitor.id)

    assert moved == 1
    assert len(store.root_jobs(account.id)) == 1
    assert store.user(visitor.id) is None    # пустая анонимная запись убрана


# ------------------------------------------------------- восстановление


def test_reset_token_is_single_use(store):
    user = store.ensure_user(None)
    token, token_hash = auth.make_reset_token()
    store.create_reset(user.id, token_hash, auth.token_expiry())

    assert store.consume_reset(token_hash) == user.id
    assert store.consume_reset(token_hash) is None


def test_expired_reset_rejected(store):
    user = store.ensure_user(None)
    _, token_hash = auth.make_reset_token()
    store.create_reset(user.id, token_hash, time.time() - 1)
    assert store.consume_reset(token_hash) is None


def test_only_hash_of_token_is_stored(store):
    """В базе не должно быть кода из письма -- только его хеш."""
    user = store.ensure_user(None)
    token, token_hash = auth.make_reset_token()
    store.create_reset(user.id, token_hash, auth.token_expiry())
    assert token != token_hash
    assert auth.hash_token(token) == token_hash


def test_password_change_purges_other_tokens(store):
    user = store.ensure_user(None)
    _, first = auth.make_reset_token()
    _, second = auth.make_reset_token()
    store.create_reset(user.id, first, auth.token_expiry())
    store.create_reset(user.id, second, auth.token_expiry())

    store.purge_resets(user.id)
    assert store.consume_reset(first) is None
    assert store.consume_reset(second) is None


# ---------------------------------------------------------------- удаление


def test_delete_removes_children(store):
    user = store.ensure_user(None)
    track = store.create_job(user.id, "Пожары.mp3", {})
    store.create_job(user.id, "гитара", {"parent": track.id, "stem": "guitar"})
    store.create_job(user.id, "бас", {"parent": track.id, "stem": "bass"})

    removed = store.delete_job(track.id)
    assert len(removed) == 3
    assert store.job(track.id) is None
    assert store.root_jobs(user.id) == []


# --------------------------------------------------------------- обращения


def test_ticket_roundtrip(store):
    user = store.ensure_user(None)
    ticket_id = store.create_ticket(user.id, "ivan@mail.ru", "refund", "Не открылся доступ")

    mine = store.user_tickets(user.id)
    assert len(mine) == 1 and mine[0]["status"] == "new"

    store.answer_ticket(ticket_id, "Вернули оплату")
    answered = store.ticket(ticket_id)
    assert answered["status"] == "answered"
    assert answered["answer"] == "Вернули оплату"
    assert answered["answered_at"]


def test_open_tickets_counted(store):
    user = store.ensure_user(None)
    store.create_ticket(user.id, "a@mail.ru", "problem", "Вопрос первый")
    second = store.create_ticket(user.id, "a@mail.ru", "idea", "Вопрос второй")
    assert store.stats()["open_tickets"] == 2
    store.answer_ticket(second, "Ответ")
    assert store.stats()["open_tickets"] == 1


def test_refund_deadline_is_ten_days():
    """Закон о защите прав потребителей отводит на ответ 10 дней."""
    assert support.deadline_days("refund") == 10


def test_overdue_ticket_is_flagged():
    created = time.time() - 11 * 86400
    level = support.urgency(created, "refund")
    assert level.overdue and level.level == "late"


def test_answered_ticket_judged_by_answer_time():
    """В истории должно быть видно, уложились ли, а не что срок давно вышел."""
    created = time.time() - 30 * 86400
    answered = created + 2 * 86400
    level = support.urgency(created, "refund", answered)
    assert not level.overdue


@pytest.mark.parametrize("bad", ["", "коротко", "x" * 5000])
def test_short_or_huge_tickets_rejected(bad):
    with pytest.raises(ValueError):
        support.validate("problem", bad)


def test_admin_rights_need_an_account(client, monkeypatch):
    """
    Права владельца привязываются к учётной записи, а не к куке.

    Раньше вход по ключу помечал текущего безымянного посетителя, и
    владелец, зашедший с телефона или почистивший куки, оказывался новым
    человеком без прав. Со стороны это выглядело как «меня выкинуло из
    админки, а пароля я не знаю», и вернуть их можно было только с сервера.
    """
    import web.app as app_module

    monkeypatch.setattr(app_module, "ADMIN_KEY", "ключ")

    # Безымянный посетитель прав не получает -- ему объясняют, почему
    refused = client.post("/api/admin/login", data={"key": "ключ"})
    assert refused.status_code == 403
    assert "учётную запись" in refused.json()["detail"]

    client.post("/api/auth/register",
                data={"email": "vladelec@naslux.ru", "password": "длинный-пароль-9"})
    assert client.post("/api/admin/login", data={"key": "ключ"}).status_code == 200
    assert client.get("/api/me").json()["isAdmin"] is True

    # Неверный ключ по-прежнему не пускает. Ключ здесь кириллический
    # намеренно: secrets.compare_digest на строках требует ASCII и на
    # таком ключе падал с TypeError -- то есть пятисотой ошибкой вместо
    # входа, и виноватым выглядел бы правильный ключ.
    assert client.post("/api/admin/login", data={"key": "не тот"}).status_code == 403
