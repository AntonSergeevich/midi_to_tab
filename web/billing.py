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

    def create_payment(self, user_id: str, amount: float, return_url: str) -> dict:
        raise NotImplementedError

    def verify_webhook(self, payload: dict) -> tuple[str, str] | None:
        """Вернуть (id платежа у провайдера, статус) или None, если это не наш случай."""
        raise NotImplementedError


class GetPlatinumProvider(PaymentProvider):
    """
    GetPlatinum -- приём карт и СБП, рассчитан на самозанятых и экспертов.

    ВНИМАНИЕ: точные адреса и поля запроса берутся из кабинета мерчанта.
    Здесь описана общая для таких сервисов схема (создать платёж ->
    получить ссылку на оплату -> принять уведомление), а конкретные имена
    полей вынесены в константы ниже -- подставьте их из документации.

    Проверить вызовы на живом магазине не удалось, поэтому перед запуском
    обязательно прогоните тестовый режим. Пока реквизиты не заданы,
    провайдер честно сообщает, что оплата не подключена.
    """

    name = "getplatinum"
    API_URL = os.environ.get("GETPLATINUM_API_URL", "")
    # Поля ответа, из которых берутся ссылка на оплату и идентификатор.
    URL_FIELD = os.environ.get("GETPLATINUM_URL_FIELD", "payment_url")
    ID_FIELD = os.environ.get("GETPLATINUM_ID_FIELD", "payment_id")

    def __init__(self) -> None:
        self.shop_id = os.environ.get("GETPLATINUM_SHOP_ID", "")
        self.secret = os.environ.get("GETPLATINUM_SECRET_KEY", "")

    def configured(self) -> bool:
        return bool(self.shop_id and self.secret and self.API_URL)

    def create_payment(self, user_id: str, amount: float, return_url: str) -> dict:
        if not self.configured():
            raise RuntimeError(
                "GetPlatinum не настроен. Задайте GETPLATINUM_API_URL, "
                "GETPLATINUM_SHOP_ID и GETPLATINUM_SECRET_KEY."
            )
        import json
        import urllib.request

        body = json.dumps(
            {
                "shop_id": self.shop_id,
                "amount": round(amount, 2),
                "currency": "RUB",
                "description": f"Подписка NASLUX, {PERIOD_DAYS} дней",
                "order_id": user_id,
                "return_url": return_url,
            }
        ).encode()
        request = urllib.request.Request(
            self.API_URL,
            data=body,
            headers={
                "Authorization": f"Bearer {self.secret}",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            data = json.load(response)
        return {
            "id": data.get(self.ID_FIELD),
            "confirmation": {"confirmation_url": data.get(self.URL_FIELD)},
            "raw": data,
        }

    def verify_webhook(self, payload: dict) -> tuple[str, str] | None:
        provider_id = payload.get(self.ID_FIELD) or payload.get("id")
        status = payload.get("status")
        if not provider_id or not status:
            return None
        # У разных сервисов успех называется по-разному
        normalised = "succeeded" if status in ("succeeded", "success", "paid", "completed") else status
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
