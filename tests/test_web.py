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


def test_signature_formula_is_found_from_a_real_notice():
    """
    Формулу подписи можно не знать заранее -- её выдаёт первое уведомление.

    Точную формулу GetPlatinum для версии 2 прочитать не удалось: их
    сайт закрыт сетевой политикой. Но угадывать и не нужно: когда
    приходит настоящее уведомление с настоящей подписью, перебор ходовых
    способов находит тот, при котором подпись сходится.
    """
    from web import billing

    secret = "секрет-магазина"
    payload = {"terminal": "153777", "order_id": "a1b2c3", "amount": "19.00",
               "status": "paid"}

    for scheme in billing.SCHEMES:
        for fields in (("terminal", "order_id", "amount", "status"),
                       ("order_id", "amount")):
            signature = billing.make_signature(payload, fields, secret, scheme)
            found = billing.detect_scheme(
                payload, signature, secret, billing.field_guesses(payload)
            )
            assert (scheme, fields) in found, (scheme, fields)

    # Чужая подпись не подходит ни под одну формулу
    assert not billing.detect_scheme(
        payload, "0" * 64, secret, billing.field_guesses(payload)
    )


def test_signature_field_never_signs_itself():
    """
    Сама подпись в подписываемую строку входить не может.

    Иначе её нельзя было бы вычислить: чтобы посчитать подпись, нужна
    подпись. Проверяем, что служебные ключи из перебора исключены.
    """
    from web import billing

    payload = {"terminal": "1", "amount": "19", "signature": "abc",
               "sign": "x", "hash": "y", "version": "2"}
    for fields in billing.field_guesses(payload):
        assert "signature" not in fields
        assert "sign" not in fields
        assert "hash" not in fields
        assert "version" not in fields


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

    def refuse(self, user_id, amount, return_url):
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
