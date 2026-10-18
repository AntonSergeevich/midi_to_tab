"""
Обращения пользователей.

Отдельный модуль ради одного: сроки ответа. Закон отводит на ответ
потребителю конкретное время, и его легко проглядеть в потоке дел.
Поэтому срок считается здесь и показывается в админке цветом, а не
держится в голове.
"""

from __future__ import annotations

import datetime as dt
import time
from dataclasses import dataclass

# Темы обращений. Срок ответа зависит от темы: требование о возврате
# денег закон ограничивает жёстче, чем обычный вопрос.
TOPICS: dict[str, dict] = {
    "refund": {
        "title": "Возврат денег",
        "days": 10,
        "note": "Закон о защите прав потребителей отводит на ответ 10 дней.",
    },
    "problem": {
        "title": "Что-то не работает",
        "days": 10,
        "note": "Отвечаем в течение 10 дней.",
    },
    "data": {
        "title": "Мои персональные данные",
        "days": 10,
        "businessDays": True,
        "note": "Запрос по 152-ФЗ: ответ в течение 10 рабочих дней.",
    },
    "idea": {
        "title": "Предложение или пожелание",
        "days": 30,
        "note": "Постараемся ответить в течение месяца.",
    },
}
DEFAULT_TOPIC = "problem"

MAX_BODY = 4000


def topic_title(key: str) -> str:
    return TOPICS.get(key, TOPICS[DEFAULT_TOPIC])["title"]


def deadline_days(key: str) -> int:
    return TOPICS.get(key, TOPICS[DEFAULT_TOPIC])["days"]


@dataclass
class Urgency:
    """Сколько осталось до конца законного срока."""

    days_left: float
    overdue: bool
    level: str      # ok | soon | late

    def as_dict(self) -> dict:
        return {"daysLeft": round(self.days_left, 1), "overdue": self.overdue,
                "level": self.level}


def _business_deadline(created_at: float, days: int) -> float:
    """Отсчитать `days` РАБОЧИХ дней от created_at, пропуская субботу и
    воскресенье (152-ФЗ про персональные данные считает срок именно так,
    в отличие от ЗОЗПП, где срок -- в календарных днях)."""
    moment = dt.datetime.fromtimestamp(created_at, tz=dt.timezone.utc)
    remaining = days
    while remaining > 0:
        moment += dt.timedelta(days=1)
        if moment.weekday() < 5:  # 0..4 -- будни
            remaining -= 1
    return moment.timestamp()


def urgency(created_at: float, topic: str, answered_at: float | None = None) -> Urgency:
    """
    Насколько срочно обращение.

    Для отвеченных считаем по моменту ответа -- чтобы в истории было
    видно, уложились или нет, а не только текущее состояние.
    """
    spec = TOPICS.get(topic, TOPICS[DEFAULT_TOPIC])
    days = spec["days"]
    # Раньше срок всегда считался в календарных днях, даже для "data" --
    # а её собственная подпись (note) честно говорит про РАБОЧИЕ дни.
    # Итог: запрос по персональным данным, поданный перед выходными, помечался
    # "просрочен" на несколько дней раньше, чем 152-ФЗ в самом деле требует
    # ответить.
    deadline = _business_deadline(created_at, days) if spec.get("businessDays") \
        else created_at + days * 86400
    moment = answered_at if answered_at else time.time()
    left = (deadline - moment) / 86400
    if left < 0:
        return Urgency(left, True, "late")
    if left < 3:
        return Urgency(left, False, "soon")
    return Urgency(left, False, "ok")


def validate(topic: str, body: str) -> tuple[str, str]:
    """Проверить обращение. Возвращает (тема, текст) в очищенном виде."""
    key = topic if topic in TOPICS else DEFAULT_TOPIC
    text = (body or "").strip()
    if len(text) < 10:
        raise ValueError("Опишите вопрос подробнее -- хотя бы пару предложений.")
    if len(text) > MAX_BODY:
        raise ValueError(f"Слишком длинно: максимум {MAX_BODY} символов.")
    return key, text
