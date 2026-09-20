"""
Отправка писем.

Нужна ровно для одного: выслать ссылку восстановления пароля. Поэтому
здесь нет ни шаблонов, ни очередей -- только SMTP и понятная ошибка,
если он не настроен.

Для России подходит SMTP Яндекса или Mail.ru. Пароль берётся не от
почтового ящика, а отдельный «пароль приложения»: так его можно отозвать,
не меняя основной.
"""

from __future__ import annotations

import os
import smtplib
import ssl
from email.message import EmailMessage


def settings() -> dict:
    return {
        "host": os.environ.get("SMTP_HOST", ""),
        "port": int(os.environ.get("SMTP_PORT", "465")),
        "user": os.environ.get("SMTP_USER", ""),
        "password": os.environ.get("SMTP_PASSWORD", ""),
        "sender": os.environ.get("SMTP_FROM", "") or os.environ.get("SMTP_USER", ""),
        "ssl": os.environ.get("SMTP_SSL", "1") != "0",
    }


def available() -> tuple[bool, str]:
    cfg = settings()
    if not (cfg["host"] and cfg["user"] and cfg["password"]):
        return False, (
            "Отправка писем не настроена: задайте SMTP_HOST, SMTP_USER и "
            "SMTP_PASSWORD. Без этого восстановление пароля не работает."
        )
    return True, ""


def send(to: str, subject: str, body: str) -> None:
    ok, why = available()
    if not ok:
        raise RuntimeError(why)
    cfg = settings()

    message = EmailMessage()
    message["From"] = cfg["sender"]
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)

    context = ssl.create_default_context()
    if cfg["ssl"]:
        with smtplib.SMTP_SSL(cfg["host"], cfg["port"], context=context, timeout=30) as smtp:
            smtp.login(cfg["user"], cfg["password"])
            smtp.send_message(message)
    else:
        with smtplib.SMTP(cfg["host"], cfg["port"], timeout=30) as smtp:
            smtp.starttls(context=context)
            smtp.login(cfg["user"], cfg["password"])
            smtp.send_message(message)


def send_reset(to: str, link: str) -> None:
    send(
        to,
        "NASLUX — восстановление пароля",
        "Здравствуйте!\n\n"
        "Вы запросили смену пароля в NASLUX. Перейдите по ссылке:\n\n"
        f"{link}\n\n"
        "Ссылка действует час и срабатывает один раз.\n"
        "Если вы ничего не запрашивали, просто не открывайте её: "
        "пароль останется прежним.\n",
    )
