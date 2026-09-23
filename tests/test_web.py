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


def test_yookassa_webhook_does_not_trust_a_forged_status(monkeypatch):
    """
    ЮKassa не подписывает уведомление -- значит, verify() обязан
    перепроверить исход у самой ЮKassa, а не поверить статусу из тела
    запроса. Иначе платёж своего собственного id с телом
    {"status": "succeeded"} мог бы прислать кто угодно.
    """
    from web import billing

    monkeypatch.setenv("YOOKASSA_SHOP_ID", "shop1")
    monkeypatch.setenv("YOOKASSA_SECRET_KEY", "secret1")
    gateway = billing.YooKassaProvider()

    def fake_pending(payment_id):
        return "pending"

    monkeypatch.setattr(gateway, "_confirmed_status", fake_pending)
    forged = {"object": {"id": "p1", "status": "succeeded"}}
    # Тело лжёт про "succeeded", но верят только ответу самой ЮKassa --
    # он и приходит в результате, настоящий "pending", а не подделанный.
    assert gateway.verify(b"{}", {}, forged) == ("p1", "pending")

    def fake_succeeded(payment_id):
        return "succeeded"

    monkeypatch.setattr(gateway, "_confirmed_status", fake_succeeded)
    assert gateway.verify(b"{}", {}, forged) == ("p1", "succeeded")


def test_yookassa_webhook_refused_without_credentials(monkeypatch):
    from web import billing

    monkeypatch.delenv("YOOKASSA_SHOP_ID", raising=False)
    monkeypatch.delenv("YOOKASSA_SECRET_KEY", raising=False)
    gateway = billing.YooKassaProvider()
    assert gateway.shop_id == "" and gateway.secret == ""
    real = {"object": {"id": "p1", "status": "succeeded"}}
    assert gateway.verify(b"{}", {}, real) is None


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


def test_getplatinum_checksum_follows_the_documentation(monkeypatch):
    """
    Контрольная подпись версии 2 -- ровно как её описывает GetPlatinum.

    HMAC-SHA256 от ТЕЛА ЗАПРОСА целиком, ключ -- API-ключ магазина,
    шестнадцатеричная строка в ВЕРХНЕМ регистре. Сверяем с эталоном,
    посчитанным независимо: совпадение с документацией важнее, чем
    внутренняя согласованность нашего кода с самим собой.
    """
    import hashlib
    import hmac

    from web import billing

    monkeypatch.setenv("GETPLATINUM_SECRET_KEY", "ключ-магазина")
    gateway = billing.GetPlatinumProvider()
    body = b'{"order_id":"a1b2","status":"paid","amount":"19.00"}'

    expected = hmac.new("ключ-магазина".encode(), body, hashlib.sha256).hexdigest().upper()
    assert gateway.checksum(body, gateway.secret) == expected
    assert expected.isupper() and len(expected) == 64


def test_getplatinum_accepts_only_a_correctly_signed_body(monkeypatch):
    """
    Подпись приходит ЗАГОЛОВКОМ, а не полем в JSON.

    В версии 2 поля checksum в теле нет вовсе. И проверять её надо по
    сырым байтам: разобрать JSON и собрать заново нельзя -- поменяется
    порядок ключей или пробелы, и подпись не сойдётся, хотя уведомление
    настоящее.
    """
    import json

    from web import billing

    monkeypatch.setenv("GETPLATINUM_SECRET_KEY", "ключ-магазина")
    gateway = billing.GetPlatinumProvider()
    body = b'{"notificationType":1,"dealId":"a1b2","isSuccess":true}'
    payload = json.loads(body)
    good = gateway.checksum(body, gateway.secret)

    assert gateway.verify(body, {"X-Checksum": good}, payload) == ("a1b2", "succeeded")
    # Заголовок ищется без оглядки на регистр
    assert gateway.verify(body, {"x-checksum": good.lower()}, payload) == ("a1b2", "succeeded")

    assert gateway.verify(body, {"X-Checksum": "A" * 64}, payload) is None
    assert gateway.verify(body, {}, payload) is None
    assert gateway.verify(b"", {"X-Checksum": good}, payload) is None

    # Пересобранный JSON -- уже другие байты, и это должно быть видно
    reserialised = json.dumps(payload).encode()
    assert reserialised != body
    assert gateway.verify(reserialised, {"X-Checksum": good}, payload) is None

    # Неуспешная оплата ничего не начисляет
    failed = b'{"notificationType":1,"dealId":"a1b2","isSuccess":false}'
    assert gateway.verify(failed, {"X-Checksum": gateway.checksum(failed, gateway.secret)},
                          json.loads(failed)) == ("a1b2", "failed")


def test_payment_amount_goes_in_kopecks(monkeypatch):
    """
    Сумма передаётся в минимальных единицах -- в копейках.

    Рубли тут передавать нельзя: сервис примет число как копейки, и
    вместо 199 рублей человек заплатит 1 рубль 99 копеек. И сумма
    заказа обязана в точности сойтись с суммой позиций.
    """
    import json

    from web import billing

    monkeypatch.setenv("GETPLATINUM_SECRET_KEY", "f" * 64)
    gateway = billing.GetPlatinumProvider()
    sent = {}

    class FakeResponse:
        def read(self):
            return json.dumps({"dealId": "d1", "formUrl": "https://pay/x",
                               "errorCode": 0}).encode()
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False

    def fake_open(request, timeout=None):
        sent["body"] = json.loads(request.data.decode())
        sent["headers"] = dict(request.headers)
        return FakeResponse()

    import urllib.request

    monkeypatch.setattr(urllib.request, "urlopen", fake_open)
    result = gateway.create_payment(
        "user-1", billing.PRICE_RUB, "https://naslux.ru/?paid=1",
        title="Подписка", notify_url="https://naslux.ru/api/webhook/getplatinum",
        email="kto@mail.ru",
    )

    body = sent["body"]
    assert body["amount"] == 19900                      # 199 рублей
    assert body["positions"][0]["price"] == 19900
    assert body["amount"] == sum(p["price"] * p["quantity"] for p in body["positions"])
    assert body["currency"] == "RUB"
    assert body["clientParams"]["clientId"] == "user-1"
    assert body["notificationUrl"].endswith("/api/webhook/getplatinum")

    # Ключ передаётся заголовком, а не в теле
    assert sent["headers"]["Authorization"] == "Bearer " + "f" * 64
    assert "f" * 64 not in json.dumps(body)

    assert result["confirmation"]["confirmation_url"] == "https://pay/x"


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


# ------------------------------------------------------- баланс (пополнение)


def test_topup_credits_exact_amount_paid(store):
    """
    Пополнение зачисляет РОВНО ту сумму, что пришла в оплате -- у неё нет
    фиксированной цены, как у тарифов в PLANS.
    """
    from web import billing

    user = store.ensure_user(None)
    billing.apply_plan(store, user.id, "topup", amount=350.0)
    user = store.user(user.id)
    assert user.balance == 350.0
    assert user.credits == 0
    assert not user.subscribed


def test_balance_unblocks_after_free_and_credits_run_out(store):
    from web import billing

    user = store.ensure_user(None)
    for _ in range(billing.FREE_SONGS):
        billing.consume(store, user)
        user = store.user(user.id)
    assert not billing.check_access(user).allowed

    billing.apply_plan(store, user.id, "topup", amount=billing.PRICE_SINGLE_RUB)
    user = store.user(user.id)
    access = billing.check_access(user)
    assert access.allowed
    assert "Баланс" in access.reason


def test_consume_spends_balance_only_after_free_and_credits(store):
    """Порядок списания: бесплатные -> купленные треки -> баланс."""
    from web import billing

    user = store.ensure_user(None)
    billing.apply_plan(store, user.id, "single")            # 1 купленный трек
    billing.apply_plan(store, user.id, "topup", amount=100.0)
    user = store.user(user.id)

    for _ in range(billing.FREE_SONGS):
        billing.consume(store, user)                        # тратим бесплатные
        user = store.user(user.id)
    assert user.credits == 1 and user.balance == 100.0

    billing.consume(store, user)                             # тратим купленный
    user = store.user(user.id)
    assert user.credits == 0 and user.balance == 100.0

    billing.consume(store, user)                             # и только теперь баланс
    user = store.user(user.id)
    assert user.balance == 100.0 - billing.PRICE_SINGLE_RUB


def test_spend_balance_never_goes_negative_under_race(store):
    """
    Регрессия по образцу mark_paid_once: два одновременных списания не
    должны оба пройти и увести баланс в минус.
    """
    user = store.ensure_user(None)
    store.add_balance(user.id, 19.0)

    first = store.spend_balance(user.id, 19.0)
    second = store.spend_balance(user.id, 19.0)

    assert first is True
    assert second is False
    assert store.user(user.id).balance == 0.0


def test_spend_free_never_exceeds_the_limit_under_race(store):
    """
    Та же гонка, что и у баланса, только для пробных песен.

    `spend_free` раньше просто прибавляла к счётчику без условия на
    текущее значение -- сколько бы запросов ни пришло одновременно с
    последней оставшейся пробной песней, каждый её бы списал, и все
    прошли бы бесплатно.
    """
    from web import billing

    user = store.ensure_user(None)
    for _ in range(billing.FREE_SONGS - 1):
        assert store.spend_free(user.id, billing.FREE_SONGS) is True

    first = store.spend_free(user.id, billing.FREE_SONGS)
    second = store.spend_free(user.id, billing.FREE_SONGS)

    assert first is True
    assert second is False
    assert store.user(user.id).free_used == billing.FREE_SONGS


def test_spend_credit_never_goes_negative_under_race(store):
    """Та же гонка для оплаченных поштучно треков."""
    user = store.ensure_user(None)
    store.add_credits(user.id, 1)

    first = store.spend_credit(user.id)
    second = store.spend_credit(user.id)

    assert first is True
    assert second is False
    assert store.user(user.id).credits == 0


def test_consume_falls_back_when_the_first_tier_loses_the_race(store):
    """
    consume() должен уметь пробовать следующий уровень, а не молча
    списывать в никуда, когда снимок `user` устарел.

    Один и тот же снимок пользователя (с одним кредитом и запасом на
    балансе) передаётся в consume() дважды подряд -- как если бы второй
    запрос начал обрабатываться до того, как первый успел обновить
    состояние. Первый вызов должен потратить кредит, второй -- откатиться
    на баланс, а не решить по устаревшему credits=1, что списывать нечего.
    """
    from web import billing

    user = store.ensure_user(None)
    for _ in range(billing.FREE_SONGS):
        store.spend_free(user.id, billing.FREE_SONGS)
    billing.apply_plan(store, user.id, "single")
    store.add_balance(user.id, billing.PRICE_SINGLE_RUB)
    stale = store.user(user.id)
    assert stale.credits == 1

    assert billing.consume(store, stale) is True
    assert billing.consume(store, stale) is True

    fresh = store.user(user.id)
    assert fresh.credits == 0
    assert fresh.balance == 0.0


def test_topup_amount_must_be_within_bounds(tmp_path, monkeypatch):
    import sys

    monkeypatch.setenv("MIDI2TAB_DATA", str(tmp_path / "data"))
    for name in [m for m in sys.modules if m.startswith("web.")]:
        del sys.modules[name]
    from fastapi.testclient import TestClient

    import web.app as app_module

    with TestClient(app_module.app) as client:
        client.post("/api/auth/register",
                    data={"email": "wallet@naslux.ru", "password": "длинный-пароль-9"})
        too_small = client.post("/api/topup", data={"amount": "1"})
        assert too_small.status_code == 400

        too_big = client.post("/api/topup", data={"amount": "999999"})
        assert too_big.status_code == 400


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


# ------------------------------------------------- разделение вторым заходом


def test_harmonic_mix_drops_drums_and_vocals(tmp_path):
    """
    Дорожка для разбора аккордов собирается без барабанов и голоса.

    Голос -- худший враг разбора гармонии: певец тянет ноту поверх
    аккорда, и она читается как надстройка, превращая трезвучие в
    септаккорд. Барабаны размазывают спектр. Когда партии уже посчитаны,
    убрать и то и другое ничего не стоит.
    """
    numpy = pytest.importorskip("numpy")
    soundfile = pytest.importorskip("soundfile")

    from midi2tab import separate

    rate = 22050
    time_axis = numpy.linspace(0, 1, rate, endpoint=False)
    stems = {}
    for name, freq in (("guitar", 440.0), ("bass", 110.0),
                       ("drums", 3000.0), ("vocals", 900.0)):
        path = str(tmp_path / f"{name}.wav")
        soundfile.write(path, numpy.sin(2 * numpy.pi * freq * time_axis) * 0.5, rate)
        stems[name] = path

    out = separate.harmonic_mix(stems, str(tmp_path / "harmony.wav"))
    assert out is not None

    audio, _ = soundfile.read(out)
    spectrum = numpy.abs(numpy.fft.rfft(audio))
    freqs = numpy.fft.rfftfreq(len(audio), 1 / rate)

    def energy(freq):
        return float(spectrum[numpy.argmin(numpy.abs(freqs - freq))])

    # Гитара и бас на месте, барабаны и голос -- нет
    assert energy(440.0) > energy(3000.0) * 20
    assert energy(110.0) > energy(900.0) * 20


def test_harmonic_mix_survives_a_missing_library(tmp_path, monkeypatch):
    """Без soundfile разбор обязан продолжиться по полному миксу, а не упасть."""
    from midi2tab import separate

    monkeypatch.setitem(__import__("sys").modules, "soundfile", None)
    assert separate.harmonic_mix({}, str(tmp_path / "x.wav")) is None


def test_tabs_from_a_full_mix_are_refused(tmp_path, monkeypatch):
    """
    Табы из полного микса предлагать нечестно.

    Basic Pitch слышит ВСЁ: вокал, барабаны, бас и гитару разом, и всё
    это раскладывается на один гриф. На реальной песне так вышло 1118
    нот по всему грифу до семнадцатого лада -- сыграть это нельзя.
    Пока разделение доступно, надо сначала разделить.
    """
    import sys

    monkeypatch.setenv("MIDI2TAB_DATA", str(tmp_path / "data"))
    for name in [m for m in sys.modules if m.startswith("web.")]:
        del sys.modules[name]
    from fastapi.testclient import TestClient

    import web.app as app_module

    monkeypatch.setattr(app_module.separate, "available", lambda: (True, ""))

    with TestClient(app_module.app) as client:
        user = app_module.storage.ensure_user(None)
        job = app_module.storage.create_job(user.id, "песня.mp3", {})
        app_module.storage.update_job(
            job.id, status="done",
            result={"isMidi": False, "parts": [{"key": "full", "label": "Весь трек"}],
                    "paths": {"parts": {"full": "/нет/такого.wav"}}},
        )
        client.cookies.set("uid", app_module.signer.dumps(user.id))

        refused = client.post(f"/api/job/{job.id}/tabs/full")
        assert refused.status_code == 409
        assert "Разделите" in refused.json()["detail"] or "разделите" in refused.json()["detail"]


def test_pages_carry_a_build_stamp(tmp_path, monkeypatch):
    """
    Самая коварная поломка при обновлении -- смешанный кеш.

    Браузер держит СТАРЫЙ player.js, а страницу получает новую. Старый
    скрипт обращается к элементам, которых в новой странице уже нет,
    падает -- и человек видит пустую ленту без аккордов и мёртвую
    кнопку "играть". Выглядит как сломанный сервер, а сломан кеш.

    Отпечаток версии в адресах css и js делает такое невозможным:
    поменялся файл -- поменялся адрес -- браузер обязан скачать заново.
    """
    import re
    import sys

    monkeypatch.setenv("MIDI2TAB_DATA", str(tmp_path / "data"))
    for name in [m for m in sys.modules if m.startswith("web.")]:
        del sys.modules[name]
    from fastapi.testclient import TestClient

    import web.app as app_module

    with TestClient(app_module.app) as client:
        for path in ("/", "/account", "/library", "/privacy", "/offer"):
            response = client.get(path)
            assert response.status_code == 200, path
            stamped = re.findall(r'/static/[\w.\-/]+\?v=\w+', response.text)
            assert stamped, f"на {path} статика без отпечатка версии"
            # Саму страницу кешировать нельзя: в ней и лежит отпечаток
            assert "no-cache" in response.headers.get("cache-control", ""), path


def test_payment_diagnostics_name_the_real_problem():
    """
    Оплата "не работает" чаще всего не из-за кода.

    Строка из примера, скопированная целиком вместо настоящего ключа,
    выглядит как заданный ключ: она непустая, и провайдер считал себя
    настроенным. Каждый платёж после этого падал бы уже на стороне
    GetPlatinum, а причина была бы не видна ниоткуда.
    """
    import os

    from web import billing

    saved = dict(os.environ)
    try:
        os.environ["GETPLATINUM_SECRET_KEY"] = ""
        gateway = billing.GetPlatinumProvider()
        assert not gateway.configured()
        assert "НЕ ЗАДАН" in gateway.diagnose()["ключ"]

        os.environ["GETPLATINUM_SECRET_KEY"] = "ваш_ключ"
        gateway = billing.GetPlatinumProvider()
        assert not gateway.configured()
        assert "ИЗ ПРИМЕРА" in gateway.diagnose()["ключ"]

        os.environ["GETPLATINUM_SECRET_KEY"] = "f3a9c21e8b7d4a06"
        gateway = billing.GetPlatinumProvider()
        assert gateway.configured()
        state = gateway.diagnose()
        assert state["ключ"] == "задан, длина 16"
        assert "f3a9c21e8b7d4a06" not in str(state)     # ключ не утекает
        assert state["терминал"] == "153777"
    finally:
        os.environ.clear()
        os.environ.update(saved)


def test_rejected_notices_are_kept_for_diagnosis(tmp_path):
    """
    Отвергнутое уведомление важнее принятого.

    Именно по нему подбирается формула подписи. Без записи причина
    отказа теряется навсегда, и остаётся гадать, почему оплата не
    доходит.
    """
    from web.storage import Storage

    store = Storage(str(tmp_path / "notices.db"))
    store.save_notice("getplatinum", {"order_id": "a1", "status": "paid"},
                      False, "подпись не сошлась")
    store.save_notice("getplatinum", {"order_id": "a2", "status": "paid"},
                      True, "succeeded")

    rows = store.notices()
    assert len(rows) == 2
    assert rows[0]["body"]["order_id"] == "a2" and rows[0]["accepted"] == 1
    assert rows[1]["accepted"] == 0 and "подпись" in rows[1]["reason"]

    # Хранится диагностика, а не архив: старое вытесняется
    for index in range(30):
        store.save_notice("getplatinum", {"order_id": f"x{index}"}, False, "шум")
    assert len(store.notices(100)) == 20


def test_pricing_page_shows_prices_without_javascript(tmp_path, monkeypatch):
    """
    Тарифы должны быть видны сразу, а не подгружаться.

    Пока цены приходили отдельным запросом, человек секунду видел
    пустоту ровно на том месте, за чем пришёл. Цены подставляет сервер
    из тех же констант, что считают оплату, -- разойтись они не могут.
    """
    import sys

    monkeypatch.setenv("MIDI2TAB_DATA", str(tmp_path / "data"))
    for name in [m for m in sys.modules if m.startswith("web.")]:
        del sys.modules[name]
    from fastapi.testclient import TestClient

    import web.app as app_module
    from web import billing

    with TestClient(app_module.app) as client:
        html = client.get("/pricing").text
        assert f"{billing.PRICE_SINGLE_RUB:.0f} ₽" in html
        assert f"{billing.PRICE_RUB:.0f} ₽" in html
        assert "{{" not in html          # подстановки не остались сырыми
        assert 'data-plan="single"' in html and 'data-plan="month"' in html

        # И ссылка на тарифы есть в меню каждой страницы
        for path in ("/", "/library", "/account", "/privacy", "/offer"):
            assert '/pricing' in client.get(path).text, path


def test_admin_user_list_is_paged_and_searchable(tmp_path):
    """
    Тысяча пользователей не должна выгружаться одним списком.

    Раньше админка забирала всех разом, причём на каждого делался
    отдельный запрос, а ради счётчика треков подгружались сами задания.
    На десятке это незаметно, на тысяче -- тысячи запросов.
    """
    from web.storage import Storage

    store = Storage(str(tmp_path / "many.db"))
    for index in range(300):
        user = store.ensure_user(None)
        if index % 10 == 0:
            store.set_flags(user.id, unlimited=True, note=f"тестировщик {index}")
        if index % 25 == 0:
            store.register(user.id, f"человек{index}@mail.ru", "x" * 80)
        for _ in range(index % 3):
            store.create_job(user.id, "песня.mp3", {})

    page = store.users_page("", 0, 50)
    assert page["total"] == 300
    assert len(page["users"]) == 50

    # Сначала те, у кого есть доступ: владельцу нужны живые люди
    assert page["users"][0][0].unlimited

    # Счётчик треков считает база, и считает верно
    for user, tracks in page["users"]:
        assert tracks == len(store.root_jobs(user.id, 500))

    # Поиск идёт по почте, номеру И заметке -- друзей подписывают словами
    assert store.users_page("человек25@", 0, 50)["total"] == 1
    assert store.users_page("тестировщик 30", 0, 50)["total"] == 1
    assert store.users_page("тестировщик", 0, 50)["total"] == 30

    # Страницы не пересекаются и покрывают всё
    first = {u.id for u, _ in store.users_page("", 0, 50)["users"]}
    second = {u.id for u, _ in store.users_page("", 50, 50)["users"]}
    assert not (first & second)


def test_failed_payment_says_what_went_wrong(tmp_path, monkeypatch):
    """
    "Не получилось" -- это не сообщение об ошибке.

    Обращение к платёжному сервису было ничем не обёрнуто: любая
    неудача там роняла пятисотую без тела, браузеру нечего было
    показать, и человек видел голое "Не получилось". Адрес API
    подбирался без документации, так что промах по нему -- самый
    вероятный исход, и назвать его надо прямо.
    """
    import sys

    monkeypatch.setenv("MIDI2TAB_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("PAYMENT_PROVIDER", "getplatinum")
    monkeypatch.setenv("GETPLATINUM_TERMINAL", "153777")
    monkeypatch.setenv("GETPLATINUM_SECRET_KEY", "f" * 64)
    for name in [m for m in sys.modules if m.startswith("web.")]:
        del sys.modules[name]
    from fastapi.testclient import TestClient

    import web.app as app_module
    from web import billing

    def refuse(self, user_id, amount, return_url, **extra):
        raise billing.PaymentError(
            "Не удалось соединиться с https://api.getplatinum.ru/...: имя не найдено",
            {"адрес": "https://api.getplatinum.ru/...", "причина": "имя не найдено"},
        )

    monkeypatch.setattr(billing.GetPlatinumProvider, "create_payment", refuse)

    with TestClient(app_module.app) as client:
        client.post("/api/auth/register",
                    data={"email": "pokupatel@naslux.ru", "password": "длинный-пароль-9"})
        response = client.post("/api/subscribe", data={"plan": "single"})

        assert response.status_code == 502
        detail = response.json()["detail"]
        assert "getplatinum" in detail and "имя не найдено" in detail

        # И попытка сохранена -- владелец увидит её в админке
        notices = app_module.storage.notices()
        assert notices and not notices[0]["accepted"]
        assert "попытка оплаты" in notices[0]["body"]


def test_a_repeated_notice_credits_only_once(tmp_path, monkeypatch):
    """
    Повторное уведомление не должно начислять второй раз.

    Платёжные сервисы повторяют уведомления, если ответ потерялся или
    пришёл не сразу, -- и это нормальное их поведение. А вот выдать за
    один платёж два трека или два месяца подписки -- уже нет. Ошибка
    нашлась на живом прогоне: то же самое уведомление, посланное дважды,
    дало человеку два трека вместо одного.
    """
    import hashlib
    import hmac
    import json
    import sys

    key = "f" * 64
    monkeypatch.setenv("MIDI2TAB_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("PAYMENT_PROVIDER", "getplatinum")
    monkeypatch.setenv("GETPLATINUM_SECRET_KEY", key)
    for name in [m for m in sys.modules if m.startswith("web.")]:
        del sys.modules[name]
    from fastapi.testclient import TestClient

    import web.app as app_module

    with TestClient(app_module.app) as client:
        user = app_module.storage.ensure_user(None)
        app_module.storage.create_payment(user.id, 19.0, "zakaz-1", plan="single")

        body = b'{"notificationType": 1, "dealId": "zakaz-1", "isSuccess": true}'
        checksum = hmac.new(key.encode(), body, hashlib.sha256).hexdigest().upper()
        headers = {"X-Checksum": checksum, "Content-Type": "application/json"}

        for _ in range(4):
            assert client.post("/api/webhook/getplatinum",
                               content=body, headers=headers).status_code == 200

        assert app_module.storage.user(user.id).credits == 1

        # Повтор виден в диагностике -- владельцу понятно, что произошло
        reasons = [n["reason"] for n in app_module.storage.notices()]
        assert any("повтор" in r for r in reasons)

        # Отменённое уведомление начислений не даёт вовсе
        cancelled = json.dumps({"notificationType": 1, "dealId": "zakaz-1",
                                "isSuccess": False}).encode()
        client.post("/api/webhook/getplatinum", content=cancelled, headers={
            "X-Checksum": hmac.new(key.encode(), cancelled, hashlib.sha256)
            .hexdigest().upper()})
        assert app_module.storage.user(user.id).credits == 1


def test_topup_webhook_credits_exact_amount_and_only_once(tmp_path, monkeypatch):
    """
    Пополнение через настоящий вебхук: зачисляется именно оплаченная
    сумма (не фиксированная цена тарифа), и повтор уведомления не
    удваивает баланс -- та же защита, что и для купленных треков.
    """
    import hashlib
    import hmac
    import sys

    key = "f" * 64
    monkeypatch.setenv("MIDI2TAB_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("PAYMENT_PROVIDER", "getplatinum")
    monkeypatch.setenv("GETPLATINUM_SECRET_KEY", key)
    for name in [m for m in sys.modules if m.startswith("web.")]:
        del sys.modules[name]
    from fastapi.testclient import TestClient

    import web.app as app_module

    with TestClient(app_module.app) as client:
        user = app_module.storage.ensure_user(None)
        app_module.storage.create_payment(user.id, 350.0, "popolnenie-1", plan="topup")

        body = b'{"notificationType": 1, "dealId": "popolnenie-1", "isSuccess": true}'
        checksum = hmac.new(key.encode(), body, hashlib.sha256).hexdigest().upper()
        headers = {"X-Checksum": checksum, "Content-Type": "application/json"}

        for _ in range(3):
            assert client.post("/api/webhook/getplatinum",
                               content=body, headers=headers).status_code == 200

        assert app_module.storage.user(user.id).balance == 350.0


def test_deal_created_notice_does_not_fail_the_payment(tmp_path, monkeypatch):
    """
    Регрессия с живого сайта: за неделю три оплаты -- и все помечены "не
    прошёл", а три уведомления отвергнуты как "платёж не найден".

    GetPlatinum шлёт на тот же адрес уведомление типа 7 "заказ создан" с
    isSuccess=false (по спецификации -- всегда) и шлёт его СРАЗУ, пока
    платёж ещё не записан у нас. Оно читалось как отказ оплаты. Статус
    меняет только тип 1, а "неудача" не перетирает уже зачисленное --
    иначе повтор успеха начислил бы второй раз.
    """
    import hashlib
    import hmac
    import json
    import sys

    key = "f" * 64
    monkeypatch.setenv("MIDI2TAB_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("PAYMENT_PROVIDER", "getplatinum")
    monkeypatch.setenv("GETPLATINUM_SECRET_KEY", key)
    for name in [m for m in sys.modules if m.startswith("web.")]:
        del sys.modules[name]
    from fastapi.testclient import TestClient

    import web.app as app_module

    def send(client, **fields):
        body = json.dumps(fields).encode()
        sign = hmac.new(key.encode(), body, hashlib.sha256).hexdigest().upper()
        return client.post("/api/webhook/getplatinum", content=body,
                           headers={"X-Checksum": sign, "Content-Type": "application/json"})

    with TestClient(app_module.app) as client:
        store = app_module.storage
        user = store.ensure_user(None)

        # "Заказ создан" обгоняет запись платежа -- это не ошибка
        assert send(client, notificationType=7, dealId="deal-1",
                    isSuccess=False).status_code == 200

        store.create_payment(user.id, 50.0, "deal-1", plan="topup")
        assert send(client, notificationType=7, dealId="deal-1",
                    isSuccess=False).status_code == 200
        assert store.payment_by_provider("deal-1")["status"] == "pending"

        assert send(client, notificationType=1, dealId="deal-1",
                    isSuccess=True).status_code == 200
        assert store.payment_by_provider("deal-1")["status"] == "succeeded"
        assert store.user(user.id).balance == 50.0

        # Неудача после успеха не перетирает его, повтор успеха не начисляет
        send(client, notificationType=1, dealId="deal-1", isSuccess=False)
        send(client, notificationType=1, dealId="deal-1", isSuccess=True)
        assert store.payment_by_provider("deal-1")["status"] == "succeeded"
        assert store.user(user.id).balance == 50.0

        assert not any(n["reason"] == "платёж не найден" for n in store.notices())


def test_startup_repairs_status_of_payments_that_were_paid(tmp_path):
    """
    Данные, испорченные старой ошибкой: провайдер прислал "оплачено",
    деньги зачислились, а запоздалый "заказ создан" перетёр статус на
    "failed". При запуске статус возвращается -- и ничего не начисляется
    повторно: деньги уже на счету.
    """
    from web.storage import Storage

    path = str(tmp_path / "repair.db")
    store = Storage(path)
    user = store.ensure_user(None)
    store.create_payment(user.id, 50.0, "deal-paid", plan="topup")
    store.create_payment(user.id, 19.0, "deal-unpaid", plan="single")
    store.add_balance(user.id, 50.0)
    store.set_payment_status(store.payment_by_provider("deal-paid")["id"], "failed")
    store.set_payment_status(store.payment_by_provider("deal-unpaid")["id"], "failed")
    store.save_notice("getplatinum", {"notificationType": 1, "dealId": "deal-paid",
                                      "isSuccess": True}, True, "succeeded")
    store.save_notice("getplatinum", {"notificationType": 7, "dealId": "deal-unpaid",
                                      "isSuccess": False}, True, "failed")

    store = Storage(path)                       # как при перезапуске службы
    assert store.payment_by_provider("deal-paid")["status"] == "succeeded"
    assert store.payment_by_provider("deal-unpaid")["status"] == "failed"
    assert store.user(user.id).balance == 50.0


def test_unlimited_owner_sees_balance_and_payment_history(tmp_path, monkeypatch):
    """
    Регрессия с живого сайта: владелец с безлимитом пополнил баланс на
    50 ₽ и купил трек за 19 ₽ -- и не увидел ни суммы, ни треков нигде.
    Безлимит проверялся первым и прятал деньги, а админка баланс вообще
    не читала из базы. Деньги должны быть видны при любом виде доступа,
    а каждый платёж -- со своим статусом.
    """
    import sys

    monkeypatch.setenv("MIDI2TAB_DATA", str(tmp_path / "data"))
    for name in [m for m in sys.modules if m.startswith("web.")]:
        del sys.modules[name]
    from fastapi.testclient import TestClient

    import web.app as app_module
    from web import billing

    with TestClient(app_module.app) as client:
        client.post("/api/auth/register",
                    data={"email": "vladelec@naslux.ru", "password": "длинный-пароль-9"})
        store = app_module.storage
        uid = store.user_by_email("vladelec@naslux.ru").id
        store.set_flags(uid, is_admin=True, unlimited=True)

        store.create_payment(uid, 50.0, "p-topup", plan="topup")
        store.mark_paid_once(store.payment_by_provider("p-topup")["id"])
        billing.apply_plan(store, uid, "topup", 50.0)
        store.create_payment(uid, 19.0, "p-single", plan="single")   # не оплачен

        me = client.get("/api/me").json()
        assert me["unlimited"] is True
        assert me["balance"] == 50.0

        payments = client.get("/api/payments").json()["payments"]
        assert len(payments) == 2
        statuses = {p["what"]: (p["amount"], p["paid"]) for p in payments}
        assert statuses["пополнение баланса"] == (50.0, True)
        assert statuses["один трек"] == (19.0, False)

        admin = client.get("/api/admin/users").json()
        row = next(u for u in admin["users"] if u["id"] == uid)
        assert row["balance"] == 50.0


# --------------------------------------------------------------- мониторинг


def test_health_is_off_without_a_token(tmp_path, monkeypatch):
    """Без MIDI2TAB_HEALTH_TOKEN проверка выключена целиком, а не открыта всем."""
    import sys

    monkeypatch.setenv("MIDI2TAB_DATA", str(tmp_path / "data"))
    monkeypatch.delenv("MIDI2TAB_HEALTH_TOKEN", raising=False)
    for name in [m for m in sys.modules if m.startswith("web.")]:
        del sys.modules[name]
    from fastapi.testclient import TestClient

    import web.app as app_module

    with TestClient(app_module.app) as client:
        response = client.get("/api/health")
        assert response.status_code == 503


def test_health_rejects_wrong_token(tmp_path, monkeypatch):
    import sys

    monkeypatch.setenv("MIDI2TAB_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("MIDI2TAB_HEALTH_TOKEN", "a" * 32)
    for name in [m for m in sys.modules if m.startswith("web.")]:
        del sys.modules[name]
    from fastapi.testclient import TestClient

    import web.app as app_module

    with TestClient(app_module.app) as client:
        no_header = client.get("/api/health")
        assert no_header.status_code == 403

        wrong = client.get("/api/health",
                           headers={"Authorization": "Bearer " + "b" * 32})
        assert wrong.status_code == 403


def test_health_reports_payment_status_and_recent_failures(tmp_path, monkeypatch):
    """
    Регрессия: у /api/admin/payment и /api/admin/notices один и тот же
    смысл, но эта проверка должна работать по токену из заголовка, а не
    по куке браузера -- иначе автоматический мониторинг не сможет
    авторизоваться без живой сессии.
    """
    import sys

    monkeypatch.setenv("MIDI2TAB_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("MIDI2TAB_HEALTH_TOKEN", "c" * 32)
    for name in [m for m in sys.modules if m.startswith("web.")]:
        del sys.modules[name]
    from fastapi.testclient import TestClient

    import web.app as app_module

    with TestClient(app_module.app) as client:
        app_module.storage.save_notice(
            "getplatinum", {"dealId": "z1"}, accepted=False, reason="подпись не сошлась"
        )
        app_module.storage.save_notice(
            "getplatinum", {"dealId": "z2"}, accepted=True
        )

        response = client.get(
            "/api/health", headers={"Authorization": "Bearer " + "c" * 32}
        )
        assert response.status_code == 200
        payload = response.json()
        assert "оплата" in payload
        assert payload["уведомлений_за_последние"] == 2
        assert payload["из_них_отклонено"] == 1

        # По версии видно, доехал ли пуш до сервера: автодеплой может
        # молча не сработать, а сайт -- работать на старом коде.
        import subprocess

        head = subprocess.run(["git", "rev-parse", "--short=7", "HEAD"],
                              capture_output=True, text=True,
                              cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        if head.returncode == 0:
            assert payload["версия"] == head.stdout.strip()


def test_no_route_is_registered_twice(tmp_path, monkeypatch):
    """
    Регрессия: /api/health когда-то был объявлен дважды -- один раз с
    токеном для мониторинга (см. выше), и один раз без, ещё с первых
    версий сайта. FastAPI отдаёт первый подходящий маршрут и молча
    игнорирует второй, поэтому старое объявление было мёртвым кодом:
    оно выглядело как открытая проверка здоровья, а на самом деле не
    отвечало никогда. Второе определение того же пути и метода -- всегда
    такая ловушка, будущую тоже стоит поймать здесь.
    """
    import sys

    monkeypatch.setenv("MIDI2TAB_DATA", str(tmp_path / "data"))
    for name in [m for m in sys.modules if m.startswith("web.")]:
        del sys.modules[name]

    import web.app as app_module

    seen = set()
    duplicates = []
    for route in app_module.app.routes:
        methods = getattr(route, "methods", None)
        path = getattr(route, "path", None)
        if not methods or not path:
            continue
        for method in methods:
            key = (path, method)
            if key in seen:
                duplicates.append(key)
            seen.add(key)
    assert not duplicates, f"путь объявлен дважды: {duplicates}"
