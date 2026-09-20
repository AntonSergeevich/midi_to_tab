"""
Учётные записи: пароли, вход, восстановление.

Пароль никогда не хранится и не сравнивается напрямую. Используется
scrypt из стандартной библиотеки: он намеренно медленный и требует
памяти, поэтому перебор украденной базы обходится дорого. Отдельная
зависимость не нужна.

Разделение ответственности: здесь только правила и проверки, без базы и
без HTTP. Это позволяет проверить самое опасное место тестами, не
поднимая ни сервер, ни хранилище.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import time
import unicodedata
from dataclasses import dataclass, field

# Параметры scrypt. n=2**15 -- около 100 мс и 32 МБ на проверку: человек
# разницы не заметит, а перебор миллиардов вариантов становится
# бессмысленно дорогим.
SCRYPT_N = 2 ** 15
SCRYPT_R = 8
SCRYPT_P = 1
SALT_BYTES = 16
KEY_BYTES = 32

MIN_PASSWORD = 8
MAX_PASSWORD = 256          # без верхней границы длинный пароль превращается
                            # в способ занять процессор
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s.]+\.[^@\s]{2,}$")

RESET_TTL = 3600            # час на восстановление
RESET_TOKEN_BYTES = 32

# Ограничение попыток входа
MAX_ATTEMPTS = 8
ATTEMPT_WINDOW = 900        # за 15 минут


class AuthError(Exception):
    """Ошибка, текст которой можно показать пользователю."""


# ------------------------------------------------------------------ пароли


def hash_password(password: str) -> str:
    """Посолить и посчитать хеш. Соль хранится рядом -- так и задумано."""
    check_password_rules(password)
    salt = secrets.token_bytes(SALT_BYTES)
    key = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P,
        dklen=KEY_BYTES, maxmem=64 * 1024 * 1024,
    )
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${salt.hex()}${key.hex()}"


def verify_password(password: str, stored: str | None) -> bool:
    """
    Проверить пароль.

    Сравнение постоянное по времени: иначе по задержке ответа можно
    угадывать хеш побайтно.
    """
    if not stored or not password:
        return False
    try:
        scheme, n, r, p, salt_hex, key_hex = stored.split("$")
        if scheme != "scrypt":
            return False
        key = hashlib.scrypt(
            password.encode("utf-8"), salt=bytes.fromhex(salt_hex),
            n=int(n), r=int(r), p=int(p), dklen=len(key_hex) // 2,
            maxmem=64 * 1024 * 1024,
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(key.hex(), key_hex)


def check_password_rules(password: str) -> None:
    if not isinstance(password, str) or len(password) < MIN_PASSWORD:
        raise AuthError(f"Пароль должен быть не короче {MIN_PASSWORD} символов.")
    if len(password) > MAX_PASSWORD:
        raise AuthError("Пароль слишком длинный.")
    if password.strip() == "":
        raise AuthError("Пароль не может состоять из одних пробелов.")


# ------------------------------------------------------------------ почта


def normalise_email(email: str) -> str:
    """
    Привести почту к единому виду.

    Без этого "Ivan@Mail.ru" и "ivan@mail.ru" заведут два разных аккаунта,
    а человек будет уверен, что у него один.
    """
    if not isinstance(email, str):
        raise AuthError("Укажите почту.")
    cleaned = unicodedata.normalize("NFKC", email).strip().lower()
    if not EMAIL_PATTERN.match(cleaned):
        raise AuthError("Похоже, в адресе почты опечатка.")
    if len(cleaned) > 254:
        raise AuthError("Адрес почты слишком длинный.")
    return cleaned


# ------------------------------------------------- ограничение попыток входа


@dataclass
class AttemptLimiter:
    """
    Простой счётчик неудачных попыток.

    Хранится в памяти процесса: при перезапуске обнуляется, зато не
    требует ни базы, ни внешнего хранилища. Для одного рабочего процесса
    этого достаточно; когда процессов станет несколько, счётчик придётся
    вынести наружу.
    """

    max_attempts: int = MAX_ATTEMPTS
    window: int = ATTEMPT_WINDOW
    _log: dict[str, list[float]] = field(default_factory=dict)

    def _recent(self, key: str, now: float) -> list[float]:
        times = [t for t in self._log.get(key, []) if now - t < self.window]
        self._log[key] = times
        return times

    def blocked(self, key: str) -> bool:
        return len(self._recent(key, time.time())) >= self.max_attempts

    def note_failure(self, key: str) -> None:
        now = time.time()
        self._recent(key, now).append(now)

    def reset(self, key: str) -> None:
        self._log.pop(key, None)

    def left(self, key: str) -> int:
        return max(0, self.max_attempts - len(self._recent(key, time.time())))


# ------------------------------------------------------- восстановление


def make_reset_token() -> tuple[str, str]:
    """
    Создать код восстановления.

    Возвращает (код для письма, хеш для базы). В базе хранится только
    хеш: если её украдут, восстановить чужой пароль по ней не выйдет.
    """
    token = secrets.token_urlsafe(RESET_TOKEN_BYTES)
    return token, hash_token(token)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def token_expiry(ttl: int = RESET_TTL) -> float:
    return time.time() + ttl
