"""
Подписка и оплата.

Логика квоты полностью здесь и покрыта тестами. Приём денег вынесен в
отдельный класс: заменить провайдера -- значит написать один класс, не
трогая остальное.

ВАЖНО про Россию. Stripe и Paddle с российскими юрлицами не работают.
Рабочие варианты: ЮKassa, Робокасса, CloudPayments, Продамус. Принимать
платежи можно как самозанятый или ИП; чек по 54-ФЗ обязателен, ЮKassa
умеет его пробивать сама. Реквизиты магазина задаются переменными
окружения, в коде их нет.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass

from .storage import Storage, User

FREE_SONGS = 2            # бесплатные песни для пробы
PRICE_RUB = 199.0         # подписка, рублей в месяц
PRICE_SINGLE_RUB = 19.0   # один трек
PERIOD_DAYS = 30

# Подписка окупается примерно с одиннадцатого трека в месяц -- разница
# достаточная, чтобы постоянным пользователям была выгодна именно она,
# и при этом разовая покупка не выглядела наказанием.
PLANS = {
    "month": {"price": PRICE_RUB, "title": f"Подписка на {PERIOD_DAYS} дней", "credits": 0},
    "single": {"price": PRICE_SINGLE_RUB, "title": "Один трек", "credits": 1},
}


@dataclass
class Access:
    """Можно ли обработать ещё одну песню и почему."""

    allowed: bool
    reason: str
    free_left: int
    subscribed: bool
    paid_until: float | None = None
    credits: int = 0

    def as_dict(self) -> dict:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "freeLeft": self.free_left,
            "subscribed": self.subscribed,
            "paidUntil": self.paid_until,
            "credits": self.credits,
            "price": PRICE_RUB,
            "priceSingle": PRICE_SINGLE_RUB,
            "freeSongs": FREE_SONGS,
        }


def check_access(user: User) -> Access:
    """
    Кому можно обработать ещё одну песню.

    Порядок проверок важен: безлимит выдаётся владельцем вручную и бьёт
    любые счётчики -- иначе собственный аккаунт и аккаунты тестировщиков
    упирались бы в ту же стену, что и обычные пользователи.
    """
    if user.unlimited:
        return Access(
            True,
            "Безлимитный доступ" + (" (администратор)" if user.is_admin else ""),
            FREE_SONGS,
            True,
            credits=user.credits,
        )
    if user.subscribed:
        return Access(
            True, "Подписка активна", user.free_left(FREE_SONGS), True,
            user.paid_until, user.credits,
        )
    left = user.free_left(FREE_SONGS)
    if left > 0:
        return Access(True, f"Пробный доступ: осталось песен — {left}", left, False,
                      credits=user.credits)
    if user.credits > 0:
        return Access(True, f"Оплачено треков: {user.credits}", 0, False,
                      credits=user.credits)
    return Access(
        False,
        f"Пробные песни закончились. Подписка — {PRICE_RUB:.0f} ₽ в месяц "
        f"или один трек за {PRICE_SINGLE_RUB:.0f} ₽.",
        0,
        False,
    )


def consume(storage: Storage, user: User) -> None:
    """
    Списать одну песню.

    Порядок: у безлимитных и подписчиков не списывается ничего; дальше
    сначала расходуются бесплатные пробы и только потом оплаченные
    поштучно треки -- иначе купленный трек сгорал бы раньше бесплатного.
    """
    if user.unlimited or user.subscribed:
        return
    if user.free_left(FREE_SONGS) > 0:
        storage.spend_free(user.id)
    elif user.credits > 0:
        storage.spend_credit(user.id)


def apply_plan(storage: Storage, user_id: str, plan: str) -> None:
    """Выдать оплаченное: либо дни подписки, либо треки."""
    spec = PLANS.get(plan, PLANS["month"])
    if spec["credits"]:
        storage.add_credits(user_id, spec["credits"])
    else:
        grant_subscription(storage, user_id)


def grant_subscription(storage: Storage, user_id: str, days: int = PERIOD_DAYS) -> float:
    """
    Продлить подписку. Если она ещё действует, срок прибавляется к остатку,
    а не обнуляет его -- иначе оплата раньше срока съедала бы оплаченные дни.
    """
    user = storage.user(user_id)
    base = time.time()
    if user and user.paid_until and user.paid_until > base:
        base = user.paid_until
    until = base + days * 86400
    storage.extend_subscription(user_id, until)
    return until


class PaymentProvider:
    """Интерфейс приёма денег."""

    name = "none"

    def configured(self) -> bool:
        return False

    def diagnose(self) -> dict:
        return {"провайдер": self.name, "готов принимать оплату": self.configured()}

    def create_payment(self, user_id: str, amount: float, return_url: str) -> dict:
        raise NotImplementedError

    def verify_webhook(self, payload: dict) -> tuple[str, str] | None:
        """Вернуть (id платежа у провайдера, статус) или None, если это не наш случай."""
        raise NotImplementedError


# ---------------------------------------------------------------- подписи

# Как разные сервисы считают контрольную подпись. Точную формулу
# GetPlatinum для версии 2 я прочитать не смог -- их сайт закрыт сетевой
# политикой моего окружения. Поэтому здесь собраны все ходовые способы:
# когда придёт первое настоящее уведомление, подходящий определится сам
# (см. detect_scheme), и его останется только записать в настройки.
#
# Различаются три вещи: чем хешируем, как склеиваем значения и куда
# девается секрет.
SCHEMES: dict[str, dict] = {
    "sha256:двоеточие:секрет_в_конце": {
        "hash": "sha256", "join": ":", "secret": "tail", "pairs": False},
    "sha256:подряд:секрет_в_конце": {
        "hash": "sha256", "join": "", "secret": "tail", "pairs": False},
    "sha256:подряд:секрет_в_начале": {
        "hash": "sha256", "join": "", "secret": "head", "pairs": False},
    "sha256:амперсанд:секрет_в_конце": {
        "hash": "sha256", "join": "&", "secret": "tail", "pairs": False},
    "sha256:ключ=значение:секрет_в_конце": {
        "hash": "sha256", "join": "&", "secret": "tail", "pairs": True},
    "md5:двоеточие:секрет_в_конце": {
        "hash": "md5", "join": ":", "secret": "tail", "pairs": False},
    "md5:подряд:секрет_в_конце": {
        "hash": "md5", "join": "", "secret": "tail", "pairs": False},
    "hmac-sha256:двоеточие": {
        "hash": "sha256", "join": ":", "secret": "key", "pairs": False},
    "hmac-sha256:подряд": {
        "hash": "sha256", "join": "", "secret": "key", "pairs": False},
    "hmac-sha256:ключ=значение": {
        "hash": "sha256", "join": "&", "secret": "key", "pairs": True},
}
DEFAULT_SCHEME = "sha256:двоеточие:секрет_в_конце"


def make_signature(data: dict, fields, secret: str, scheme: str) -> str:
    """Посчитать подпись по названному способу."""
    import hashlib
    import hmac

    spec = SCHEMES.get(scheme, SCHEMES[DEFAULT_SCHEME])
    parts = [
        f"{field}={data.get(field, '')}" if spec["pairs"] else str(data.get(field, ""))
        for field in fields
    ]
    if spec["secret"] == "head":
        parts.insert(0, secret)
    elif spec["secret"] == "tail":
        parts.append(secret)

    message = spec["join"].join(parts).encode()
    if spec["secret"] == "key":
        return hmac.new(secret.encode(), message, spec["hash"]).hexdigest()
    return hashlib.new(spec["hash"], message).hexdigest()


def detect_scheme(payload: dict, signature: str, secret: str,
                  fields_guesses) -> list[tuple[str, tuple]]:
    """
    Подобрать формулу по настоящему уведомлению.

    Перебираются все способы и все правдоподобные наборы полей. Когда
    подпись сойдётся -- формула найдена, и гадать больше не нужно.
    Возвращает список подошедших пар (способ, поля).
    """
    import secrets as _secrets

    given = (signature or "").strip().lower()
    if not given or not secret:
        return []
    found = []
    for scheme in SCHEMES:
        for fields in fields_guesses:
            candidate = make_signature(payload, fields, secret, scheme)
            if _secrets.compare_digest(candidate, given):
                found.append((scheme, tuple(fields)))
    return found


def field_guesses(payload: dict) -> list[tuple]:
    """
    Правдоподобные наборы полей для подписи.

    Служебные ключи -- саму подпись и её номер версии -- в неё не входят
    никогда. Перебираем: все поля по алфавиту, все в порядке прихода, и
    ходовые сочетания из терминала, заказа, суммы и статуса.
    """
    skip = {"signature", "sign", "sig", "hash", "checksum", "token", "version"}
    keys = [k for k in payload if k.lower() not in skip]
    ordered = tuple(keys)
    alphabet = tuple(sorted(keys))
    guesses = [ordered, alphabet]
    for combo in (
        ("terminal", "order_id", "amount", "status"),
        ("terminal", "order_id", "amount"),
        ("order_id", "amount", "status"),
        ("terminal", "amount", "order_id", "status"),
        ("amount", "order_id", "terminal"),
        ("order_id", "amount"),
        ("terminal", "order_id"),
    ):
        if all(field in payload for field in combo):
            guesses.append(combo)
    seen, unique = set(), []
    for guess in guesses:
        if guess not in seen:
            seen.add(guess)
            unique.append(guess)
    return unique


class GetPlatinumProvider(PaymentProvider):
    """
    GetPlatinum, API версии 2.

    Терминал задаётся GETPLATINUM_TERMINAL (по умолчанию -- наш), секрет --
    GETPLATINUM_SECRET_KEY. Пока секрета нет, провайдер честно сообщает,
    что оплата не подключена, и ничего не имитирует.

    ВАЖНО про подпись. В версии 2 контрольная сумма считается иначе, чем в
    первой, и точный состав полей берётся из документации сервиса. Поэтому
    он вынесен в SIGN_FIELDS и переопределяется переменной окружения
    GETPLATINUM_SIGN_FIELDS -- поправить порядок можно, не трогая код и не
    выкладывая новую версию. Проверить вызовы на живом магазине пока не
    удалось, поэтому перед первым настоящим платежом обязательно прогоните
    тестовый режим.
    """

    name = "getplatinum"
    API_URL = os.environ.get(
        "GETPLATINUM_API_URL", "https://api.getplatinum.ru/v2/payment/create"
    )
    # Поля ответа, из которых берутся ссылка на оплату и идентификатор.
    URL_FIELD = os.environ.get("GETPLATINUM_URL_FIELD", "payment_url")
    ID_FIELD = os.environ.get("GETPLATINUM_ID_FIELD", "payment_id")
    # Порядок полей в строке, из которой считается подпись. Секрет
    # добавляется последним -- так устроено у большинства подобных сервисов.
    SIGN_FIELDS = tuple(
        os.environ.get(
            "GETPLATINUM_SIGN_FIELDS", "terminal,order_id,amount,currency"
        ).split(",")
    )
    CALLBACK_SIGN_FIELDS = tuple(
        os.environ.get(
            "GETPLATINUM_CALLBACK_SIGN_FIELDS", "terminal,order_id,amount,status"
        ).split(",")
    )
    SIGN_FIELD = os.environ.get("GETPLATINUM_SIGN_FIELD", "signature")
    SCHEME = os.environ.get("GETPLATINUM_SCHEME", DEFAULT_SCHEME)

    def __init__(self) -> None:
        self.terminal = os.environ.get("GETPLATINUM_TERMINAL", "153777")
        self.secret = os.environ.get("GETPLATINUM_SECRET_KEY", "")

    # Значения из примера настроек. Если ключ равен одному из них, значит,
    # строку скопировали целиком, не заменив на настоящий ключ, -- и
    # честнее сказать это прямо, чем делать вид, что оплата настроена, и
    # ронять каждый платёж.
    PLACEHOLDERS = frozenset(
        {"ваш_ключ", "ваш ключ", "your_key", "secret", "xxx", "changeme", "..."}
    )

    def configured(self) -> bool:
        return bool(self.terminal and self.secret and self.API_URL
                    and self.secret.strip().lower() not in self.PLACEHOLDERS)

    def diagnose(self) -> dict:
        """
        Что именно видит сервер. Ключ не показывается -- только его длина:
        по ней видно, задан он или туда попала строка из примера.
        """
        secret = self.secret.strip()
        return {
            "провайдер": self.name,
            "терминал": self.terminal or "НЕ ЗАДАН",
            "ключ": (
                "НЕ ЗАДАН" if not secret
                else "ЭТО СТРОКА ИЗ ПРИМЕРА, а не ключ"
                if secret.lower() in self.PLACEHOLDERS
                else f"задан, длина {len(secret)}"
            ),
            "адрес API": self.API_URL,
            "способ подписи": self.SCHEME,
            "поля подписи": ",".join(self.SIGN_FIELDS),
            "поля подписи уведомления": ",".join(self.CALLBACK_SIGN_FIELDS),
            "адрес для уведомлений": "/api/webhook/getplatinum",
            "готов принимать оплату": self.configured(),
        }

    def sign(self, data: dict, fields) -> str:
        """
        Контрольная подпись по выбранному способу.

        Отсутствующее поле даёт пустую строку, а не пропускается: иначе
        подпись зависела бы от того, какие поля сервис решил прислать.
        """
        return make_signature(data, fields, self.secret, self.SCHEME)

    def guess_scheme(self, payload: dict) -> list[tuple[str, tuple]]:
        """Подобрать формулу подписи по настоящему уведомлению."""
        return detect_scheme(
            payload, str(payload.get(self.SIGN_FIELD, "")),
            self.secret, field_guesses(payload),
        )

    def create_payment(self, user_id: str, amount: float, return_url: str) -> dict:
        if not self.configured():
            raise RuntimeError(
                "GetPlatinum не настроен. Задайте GETPLATINUM_SECRET_KEY "
                "(и при необходимости GETPLATINUM_TERMINAL)."
            )
        import json
        import urllib.request
        import uuid

        payload = {
            "terminal": self.terminal,
            "order_id": f"{user_id}-{uuid.uuid4().hex[:8]}",
            "amount": f"{amount:.2f}",
            "currency": "RUB",
            "description": f"NASLUX, разбор песен",
            "success_url": return_url,
            "fail_url": return_url,
        }
        payload[self.SIGN_FIELD] = self.sign(payload, self.SIGN_FIELDS)

        request = urllib.request.Request(
            self.API_URL,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            data = json.load(response)
        return {
            "id": data.get(self.ID_FIELD) or payload["order_id"],
            "confirmation": {"confirmation_url": data.get(self.URL_FIELD)},
            "raw": data,
        }

    def verify_webhook(self, payload: dict) -> tuple[str, str] | None:
        """
        Проверить уведомление об оплате.

        Подпись проверяется обязательно: без неё выдать себе подписку
        сможет кто угодно, кто знает адрес обработчика. Сравнение
        постоянное по времени -- подбирать подпись по знакам бессмысленно.
        """
        import secrets

        provider_id = payload.get(self.ID_FIELD) or payload.get("order_id") or payload.get("id")
        status = payload.get("status")
        if not provider_id or not status:
            return None

        given = str(payload.get(self.SIGN_FIELD, ""))
        if not self.secret or not given:
            return None
        if not secrets.compare_digest(
            given.lower(), self.sign(payload, self.CALLBACK_SIGN_FIELDS)
        ):
            return None

        # У разных сервисов успех называется по-разному
        normalised = (
            "succeeded"
            if str(status).lower() in ("succeeded", "success", "paid", "completed", "confirmed")
            else str(status)
        )
        return str(provider_id), normalised


class YooKassaProvider(PaymentProvider):
    """
    ЮKassa. Реквизиты берутся из переменных окружения:
        YOOKASSA_SHOP_ID, YOOKASSA_SECRET_KEY

    Пока они не заданы, провайдер считается ненастроенным, и приложение
    честно пишет об этом вместо имитации оплаты.
    """

    name = "yookassa"
    API_URL = "https://api.yookassa.ru/v3/payments"

    def __init__(self) -> None:
        self.shop_id = os.environ.get("YOOKASSA_SHOP_ID", "")
        self.secret = os.environ.get("YOOKASSA_SECRET_KEY", "")

    def configured(self) -> bool:
        return bool(self.shop_id and self.secret)

    def create_payment(self, user_id: str, amount: float, return_url: str) -> dict:
        if not self.configured():
            raise RuntimeError(
                "Приём оплаты не настроен. Задайте YOOKASSA_SHOP_ID и "
                "YOOKASSA_SECRET_KEY, см. README."
            )
        import base64
        import json
        import urllib.request
        import uuid

        body = json.dumps(
            {
                "amount": {"value": f"{amount:.2f}", "currency": "RUB"},
                "capture": True,
                "confirmation": {"type": "redirect", "return_url": return_url},
                "description": f"Подписка NASLUX, {PERIOD_DAYS} дней",
                "metadata": {"user_id": user_id},
            }
        ).encode()
        token = base64.b64encode(f"{self.shop_id}:{self.secret}".encode()).decode()
        request = urllib.request.Request(
            self.API_URL,
            data=body,
            headers={
                "Authorization": f"Basic {token}",
                "Idempotence-Key": uuid.uuid4().hex,
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)

    def verify_webhook(self, payload: dict) -> tuple[str, str] | None:
        obj = payload.get("object") or {}
        provider_id = obj.get("id")
        status = obj.get("status")
        if not provider_id or not status:
            return None
        return provider_id, status


PROVIDERS = {
    "yookassa": YooKassaProvider,
    "getplatinum": GetPlatinumProvider,
}


def provider() -> PaymentProvider:
    """
    Какой сервис приёма оплаты использовать.

    Выбирается переменной PAYMENT_PROVIDER. Если она не задана, берётся
    первый настроенный -- чтобы смена сервиса не требовала правок в коде.
    """
    chosen = os.environ.get("PAYMENT_PROVIDER", "").strip().lower()
    if chosen in PROVIDERS:
        return PROVIDERS[chosen]()
    for factory in PROVIDERS.values():
        candidate = factory()
        if candidate.configured():
            return candidate
    return YooKassaProvider()
