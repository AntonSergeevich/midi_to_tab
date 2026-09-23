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

# Пополнение баланса произвольной суммой -- третий способ оплаты рядом с
# разовым треком и подпиской: деньги списываются по цене трека в момент
# запуска разбора, а не сразу при пополнении. Границы -- чтобы не пополнить
# по опечатке на копейку или на сумму, которую потом трудно вернуть.
TOPUP_MIN_RUB = 50.0
TOPUP_MAX_RUB = 10000.0

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
    balance: float = 0.0

    def as_dict(self) -> dict:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "freeLeft": self.free_left,
            "subscribed": self.subscribed,
            "paidUntil": self.paid_until,
            "credits": self.credits,
            "balance": self.balance,
            "price": PRICE_RUB,
            "priceSingle": PRICE_SINGLE_RUB,
            "freeSongs": FREE_SONGS,
            "topupMin": TOPUP_MIN_RUB,
            "topupMax": TOPUP_MAX_RUB,
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
            balance=user.balance,
        )
    if user.subscribed:
        return Access(
            True, "Подписка активна", user.free_left(FREE_SONGS), True,
            user.paid_until, user.credits, balance=user.balance,
        )
    left = user.free_left(FREE_SONGS)
    if left > 0:
        return Access(True, f"Пробный доступ: осталось песен — {left}", left, False,
                      credits=user.credits, balance=user.balance)
    if user.credits > 0:
        return Access(True, f"Оплачено треков: {user.credits}", 0, False,
                      credits=user.credits, balance=user.balance)
    if user.balance >= PRICE_SINGLE_RUB:
        return Access(True, f"Баланс: {user.balance:.0f} ₽", 0, False,
                      credits=user.credits, balance=user.balance)
    return Access(
        False,
        f"Пробные песни закончились. Подписка — {PRICE_RUB:.0f} ₽ в месяц, "
        f"один трек за {PRICE_SINGLE_RUB:.0f} ₽, или пополните баланс.",
        0,
        False,
        balance=user.balance,
    )


def consume(storage: Storage, user: User) -> None:
    """
    Списать одну песню.

    Порядок: у безлимитных и подписчиков не списывается ничего; дальше
    сначала расходуются бесплатные пробы, потом оплаченные поштучно
    треки, и только потом баланс -- иначе уже купленный трек или
    пополнение сгорали бы раньше бесплатного и друг друга не в том
    порядке, в котором человек за них платил.
    """
    if user.unlimited or user.subscribed:
        return
    if user.free_left(FREE_SONGS) > 0:
        storage.spend_free(user.id)
    elif user.credits > 0:
        storage.spend_credit(user.id)
    elif user.balance >= PRICE_SINGLE_RUB:
        storage.spend_balance(user.id, PRICE_SINGLE_RUB)


def apply_plan(storage: Storage, user_id: str, plan: str, amount: float | None = None) -> None:
    """
    Выдать оплаченное: дни подписки, поштучные треки или пополнение баланса.

    Пополнение -- особый случай: суммы произвольные, в PLANS их нет, и
    зачисляется ровно то, что реально пришло в оплате (amount), а не
    какая-то заранее заданная цена.
    """
    if plan == "topup":
        storage.add_balance(user_id, amount or 0.0)
        return
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


class PaymentError(RuntimeError):
    """
    Понятная ошибка приёма оплаты.

    Несёт с собой подробности для админки: адрес, код ответа, тело. Без
    них человек видит только "не получилось" и не может ничего сделать,
    а владелец -- понять, что именно чинить.
    """

    def __init__(self, message: str, details: dict | None = None) -> None:
        super().__init__(message)
        self.details = details or {}


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

    def verify(self, raw: bytes, headers, payload: dict) -> tuple[str, str] | None:
        """
        Проверить уведомление целиком: тело, заголовки, разобранный JSON.

        Сырое тело нужно потому, что подпись может считаться именно от
        него -- байт в байт, как прислали. Пересобрать JSON и посчитать
        подпись от результата нельзя: порядок ключей и пробелы изменятся,
        и подпись не сойдётся, хотя уведомление настоящее.
        """
        return self.verify_webhook(payload)


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
    GetPlatinum, API версии 2 -- по официальной спецификации.

    Адрес: https://<магазин>.getplatinum.ru/api/public/v2/pay
    Метод: POST /init-payment-url -- возвращает ссылку на платёжную форму.
    Ключ передаётся заголовком Authorization: Bearer <ключ>.
    Уведомление приходит на notificationUrl, подписано заголовком
    X-Checksum: HMAC-SHA256 от тела, верхний регистр.
    """

    name = "getplatinum"
    CHECKSUM_HEADER = "X-Checksum"

    def __init__(self) -> None:
        self.terminal = os.environ.get("GETPLATINUM_TERMINAL", "153777")
        self.secret = os.environ.get("GETPLATINUM_SECRET_KEY", "")
        # Поддомен магазина -- тот, на котором открывается личный кабинет.
        self.shop = os.environ.get("GETPLATINUM_SHOP", "s-poryadok")
        self.api_url = os.environ.get("GETPLATINUM_API_URL") or (
            f"https://{self.shop}.getplatinum.ru/api/public/v2/pay/init-payment-url"
        )
        # Ставка НДС и категория позиции для кассового чека. Это налоговый
        # вопрос, а не технический: у самозанятого НДС не применяется
        # ("none"), но ставку и категорию владелец обязан сверить со своей
        # заявкой на подключение -- ошибка тут стоит нарушения учёта.
        self.vat = os.environ.get("GETPLATINUM_VAT", "none")
        self.prefix = int(os.environ.get("GETPLATINUM_PREFIX", "3"))

    PLACEHOLDERS = frozenset(
        {"ваш_ключ", "ваш ключ", "your_key", "secret", "xxx", "changeme", "..."}
    )

    def configured(self) -> bool:
        return bool(self.secret and self.api_url
                    and self.secret.strip().lower() not in self.PLACEHOLDERS)

    def diagnose(self) -> dict:
        secret = self.secret.strip()
        return {
            "провайдер": self.name,
            "магазин": self.shop,
            "терминал": self.terminal or "не задан",
            "ключ": (
                "НЕ ЗАДАН" if not secret
                else "ЭТО СТРОКА ИЗ ПРИМЕРА, а не ключ"
                if secret.lower() in self.PLACEHOLDERS
                else f"задан, длина {len(secret)}"
            ),
            "адрес создания платежа": self.api_url,
            "ставка НДС в чеке": self.vat,
            "категория позиции": self.prefix,
            "подпись уведомления": "HMAC-SHA256 от тела, заголовок X-Checksum",
            "адрес для уведомлений": "/api/webhook/getplatinum",
            "готов принимать оплату": self.configured(),
        }

    # ----------------------------------------------------------- создание

    @staticmethod
    def checksum(raw: bytes, secret: str) -> str:
        """
        Контрольная подпись версии 2.

        HMAC-SHA256 от ТЕЛА запроса целиком, ключ -- API-ключ магазина,
        шестнадцатеричная строка в верхнем регистре. Тело берётся байт в
        байт: пересобрать JSON и считать подпись от результата нельзя --
        поменяется порядок ключей или пробелы, и подпись не сойдётся.
        """
        import hashlib
        import hmac

        return hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest().upper()

    def create_payment(self, user_id: str, amount: float, return_url: str,
                       title: str = "", notify_url: str = "",
                       email: str = "", fail_url: str = "") -> dict:
        if not self.configured():
            raise PaymentError(
                "GetPlatinum не настроен: нет GETPLATINUM_SECRET_KEY.",
                {"адрес": self.api_url},
            )
        import json
        import urllib.error
        import urllib.request
        import uuid

        # Сумма -- в КОПЕЙКАХ, и она обязана в точности сойтись с суммой
        # позиций. Рубли тут передавать нельзя: сервис примет число как
        # копейки, и вместо 199 рублей человек заплатит 1 рубль 99 копеек.
        kopecks = int(round(amount * 100))
        deal_id = f"{user_id[:12]}-{uuid.uuid4().hex[:10]}"
        name = title or "Разбор песни на аккорды и табы"

        payload = {
            "dealId": deal_id,
            "currency": "RUB",
            "amount": kopecks,
            "positions": [{
                "prefix": self.prefix,
                "name": name,
                "price": kopecks,
                "quantity": 1,
                "vat": self.vat,
            }],
            "clientParams": {"clientId": user_id, **({"email": email} if email else {})},
            "notificationUrl": notify_url,
            # Раньше оба адреса совпадали, и неудачная оплата возвращала
            # человека туда же, куда удачная, -- "перекинуло на сайт"
            # выглядело как успех. Редирект сам по себе оплату не
            # подтверждает (это делает только уведомление), но хотя бы
            # честно говорит, что форма оплаты закрылась с отказом.
            "successUrl": return_url,
            "failUrl": fail_url or return_url,
        }

        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            self.api_url,
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.secret}",
            },
        )

        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                text = response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", "replace")[:400]
            raise PaymentError(
                f"GetPlatinum ответил ошибкой {error.code} на {self.api_url}. "
                f"Ответ: {detail or 'пусто'}",
                {"адрес": self.api_url, "код": error.code, "ответ": detail,
                 "запрос": payload},
            ) from error
        except urllib.error.URLError as error:
            raise PaymentError(
                f"Не удалось соединиться с {self.api_url}: {error.reason}",
                {"адрес": self.api_url, "причина": str(error.reason)},
            ) from error

        try:
            data = json.loads(text)
        except ValueError as error:
            raise PaymentError(
                f"GetPlatinum вернул не JSON: {text[:300]}",
                {"адрес": self.api_url, "ответ": text[:400]},
            ) from error

        # errorCode = 0 означает успех; всё остальное -- отказ с причиной
        if data.get("errorCode"):
            raise PaymentError(
                f"GetPlatinum отказал: {data.get('errorMessage') or data.get('errorCode')}",
                {"адрес": self.api_url, "ответ": data, "запрос": payload},
            )

        link = data.get("formUrl")
        if not link:
            raise PaymentError(
                "GetPlatinum не вернул ссылку на оплату (поле formUrl).",
                {"адрес": self.api_url, "ответ": data},
            )
        return {
            "id": data.get("dealId") or deal_id,
            "confirmation": {"confirmation_url": link},
            "raw": data,
        }

    # -------------------------------------------------------- уведомление

    def verify(self, raw: bytes, headers, payload: dict) -> tuple[str, str] | None:
        """
        Принять уведомление, только если подпись сошлась.

        Подпись лежит в заголовке, а не в теле: в версии 2 поля checksum
        в JSON нет вовсе. Сравнение постоянное по времени.
        """
        import secrets as _secrets

        if not self.secret or not raw:
            return None
        given = ""
        if headers is not None:
            given = (headers.get(self.CHECKSUM_HEADER)
                     or headers.get(self.CHECKSUM_HEADER.lower()) or "")
        expected = self.checksum(raw, self.secret)
        if not given or not _secrets.compare_digest(given.strip().upper(), expected):
            return None
        return self._result(payload)

    def verify_webhook(self, payload: dict) -> tuple[str, str] | None:
        """Без сырого тела подпись не проверить -- значит, и принимать нельзя."""
        return None

    def _result(self, payload: dict) -> tuple[str, str] | None:
        """
        Что именно сообщили.

        В версии 2 исход оплаты -- булево поле isSuccess, а не строка
        статуса: заказ либо оплачен, либо нет.
        """
        deal_id = payload.get("dealId")
        if not deal_id:
            return None
        # На один notificationUrl приходят уведомления РАЗНЫХ типов, и
        # isSuccess у них значит разное. Тип 7 ("заказ создан") приходит
        # сразу после создания заказа и по спецификации ВСЕГДА несёт
        # isSuccess=false -- оплаты ещё не было. Раньше это читалось как
        # "оплата не прошла", и каждый платёж помечался неудавшимся ещё до
        # того, как человек открыл форму. Исход оплаты -- только тип 1.
        kind = payload.get("notificationType")
        if kind == 7:
            return str(deal_id), "created"
        if kind not in (None, 1):
            return str(deal_id), f"type-{kind}"
        success = payload.get("isSuccess")
        if success is None:
            return None
        return str(deal_id), "succeeded" if success else "failed"


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
