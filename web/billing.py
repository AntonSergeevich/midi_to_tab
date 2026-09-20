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

FREE_SONGS = 2           # бесплатные песни для пробы
PRICE_RUB = 199.0        # рублей в месяц
PERIOD_DAYS = 30


@dataclass
class Access:
    """Можно ли обработать ещё одну песню и почему."""

    allowed: bool
    reason: str
    free_left: int
    subscribed: bool
    paid_until: float | None = None

    def as_dict(self) -> dict:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "freeLeft": self.free_left,
            "subscribed": self.subscribed,
            "paidUntil": self.paid_until,
            "price": PRICE_RUB,
            "freeSongs": FREE_SONGS,
        }


def check_access(user: User) -> Access:
    """Главное правило: подписка -- без ограничений, иначе две песни на пробу."""
    if user.subscribed:
        return Access(True, "Подписка активна", user.free_left(FREE_SONGS), True, user.paid_until)
    left = user.free_left(FREE_SONGS)
    if left > 0:
        return Access(
            True,
            f"Пробный доступ: осталось песен — {left}",
            left,
            False,
        )
    return Access(
        False,
        f"Пробные песни закончились. Подписка — {PRICE_RUB:.0f} ₽ в месяц.",
        0,
        False,
    )


def consume(storage: Storage, user: User) -> None:
    """Списать одну пробную песню. У подписчика ничего не списывается."""
    if not user.subscribed:
        storage.spend_free(user.id)


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
                "description": f"Подписка MidiToTab, {PERIOD_DAYS} дней",
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


def provider() -> PaymentProvider:
    return YooKassaProvider()
