"""
Обращения пользователей.

Отдельный модуль ради одного: сроки ответа. Закон отводит на ответ
потребителю конкретное время, и его легко проглядеть в потоке дел.
Поэтому срок считается здесь и показывается в админке цветом, а не
держится в голове.
"""

from __future__ import annotations

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


def urgency(created_at: float, topic: str, answered_at: float | None = None) -> Urgency:
    """
    Насколько срочно обращение.

    Для отвеченных считаем по моменту ответа -- чтобы в истории было
    видно, уложились или нет, а не только текущее состояние.
    """
    limit = deadline_days(topic) * 86400
    moment = answered_at if answered_at else time.time()
    left = (created_at + limit - moment) / 86400
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
