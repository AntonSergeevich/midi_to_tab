"""
Хранилище: пользователи, задания, подписки.

SQLite выбран сознательно: на старте сервиса это один файл без отдельной
СУБД, а когда пойдёт нагрузка, схема переносится в PostgreSQL почти без
изменений -- запросы здесь обычные, без диалектных особенностей.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id           TEXT PRIMARY KEY,
    created_at   REAL NOT NULL,
    free_used    INTEGER NOT NULL DEFAULT 0,
    paid_until   REAL,
    email        TEXT,
    is_admin     INTEGER NOT NULL DEFAULT 0,
    unlimited    INTEGER NOT NULL DEFAULT 0,
    note         TEXT,
    credits      INTEGER NOT NULL DEFAULT 0,
    balance      REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS jobs (
    id           TEXT PRIMARY KEY,
    user_id      TEXT NOT NULL,
    created_at   REAL NOT NULL,
    filename     TEXT NOT NULL,
    status       TEXT NOT NULL,
    stage        TEXT NOT NULL DEFAULT '',
    progress     REAL NOT NULL DEFAULT 0,
    error        TEXT,
    settings     TEXT NOT NULL DEFAULT '{}',
    result       TEXT,
    counted      INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS payments (
    id           TEXT PRIMARY KEY,
    user_id      TEXT NOT NULL,
    created_at   REAL NOT NULL,
    amount       REAL NOT NULL,
    status       TEXT NOT NULL,
    provider_id  TEXT,
    plan         TEXT NOT NULL DEFAULT 'month',
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS resets (
    token_hash   TEXT PRIMARY KEY,
    user_id      TEXT NOT NULL,
    created_at   REAL NOT NULL,
    expires_at   REAL NOT NULL,
    used_at      REAL,
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS tickets (
    id           TEXT PRIMARY KEY,
    user_id      TEXT NOT NULL,
    created_at   REAL NOT NULL,
    email        TEXT,
    topic        TEXT NOT NULL,
    body         TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'new',
    answer       TEXT,
    answered_at  REAL,
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS invites (
    code         TEXT PRIMARY KEY,
    created_at   REAL NOT NULL,
    created_by   TEXT NOT NULL,
    uses_left    INTEGER NOT NULL DEFAULT 1,
    used         INTEGER NOT NULL DEFAULT 0,
    note         TEXT
);

CREATE TABLE IF NOT EXISTS notices (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at   REAL NOT NULL,
    provider     TEXT NOT NULL,
    body         TEXT NOT NULL,
    accepted     INTEGER NOT NULL DEFAULT 0,
    reason       TEXT
);

CREATE INDEX IF NOT EXISTS tickets_status ON tickets(status, created_at);
CREATE INDEX IF NOT EXISTS jobs_user ON jobs(user_id, created_at);
-- Почта уникальна на уровне базы: две учётные записи с одним адресом
-- сделали бы восстановление пароля неоднозначным.
CREATE UNIQUE INDEX IF NOT EXISTS users_email ON users(email) WHERE email IS NOT NULL;
"""


@dataclass
class User:
    id: str
    created_at: float
    free_used: int
    paid_until: float | None
    email: str | None = None
    is_admin: bool = False
    unlimited: bool = False      # безлимит: друзья, тестировщики, сам владелец
    note: str = ""               # кто это -- видно только в админке
    credits: int = 0             # оплаченные поштучно треки
    balance: float = 0.0         # пополненный баланс, рублей -- списывается за разбор
    studio_credits: int = 0      # генерации Студии из купленного пакета
    password_hash: str | None = None
    registered_at: float | None = None

    @property
    def registered(self) -> bool:
        """Завёл ли человек учётную запись или остаётся анонимным."""
        return bool(self.email and self.password_hash)

    @property
    def subscribed(self) -> bool:
        return bool(self.paid_until and self.paid_until > time.time())

    def free_left(self, limit: int) -> int:
        return max(0, limit - self.free_used)


@dataclass
class Job:
    id: str
    user_id: str
    created_at: float
    filename: str
    status: str            # queued | running | done | error
    stage: str = ""
    progress: float = 0.0  # доля выполненного, 0..100
    error: str | None = None
    settings: dict = field(default_factory=dict)
    result: dict | None = None
    counted: bool = False


class Storage:
    def __init__(self, path: str) -> None:
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(SCHEMA)
            self._migrate(conn)

    @staticmethod
    def _migrate(conn) -> None:
        """
        Дописать колонки, появившиеся позже.

        База у работающего сервиса уже содержит пользователей, и пересоздать
        её нельзя -- поэтому недостающие колонки добавляются по месту.
        """
        payment_columns = {row["name"] for row in conn.execute("PRAGMA table_info(payments)")}
        if "plan" not in payment_columns:
            conn.execute("ALTER TABLE payments ADD COLUMN plan TEXT NOT NULL DEFAULT 'month'")
        # Комиссия платёжного сервиса, рублей: приходит в уведомлении об
        # оплате. Без неё в отчёте видна только выручка, а не то, что
        # остаётся на руках.
        if "commission" not in payment_columns:
            conn.execute("ALTER TABLE payments ADD COLUMN commission REAL NOT NULL DEFAULT 0")

        job_columns = {row["name"] for row in conn.execute("PRAGMA table_info(jobs)")}
        if "progress" not in job_columns:
            conn.execute("ALTER TABLE jobs ADD COLUMN progress REAL NOT NULL DEFAULT 0")

        existing = {row["name"] for row in conn.execute("PRAGMA table_info(users)")}
        for column, definition in (
            ("is_admin", "INTEGER NOT NULL DEFAULT 0"),
            ("unlimited", "INTEGER NOT NULL DEFAULT 0"),
            ("note", "TEXT"),
            ("credits", "INTEGER NOT NULL DEFAULT 0"),
            ("password_hash", "TEXT"),
            ("registered_at", "REAL"),
            ("balance", "REAL NOT NULL DEFAULT 0"),
            ("studio_credits", "INTEGER NOT NULL DEFAULT 0"),
        ):
            if column not in existing:
                conn.execute(f"ALTER TABLE users ADD COLUMN {column} {definition}")

        # Починка данных после ошибки с уведомлением "заказ создан": оно
        # приходило запоздалым повтором ПОСЛЕ "оплачено" и перетирало
        # статус уже оплаченного и зачисленного платежа на "failed".
        # Деньги при этом начислялись -- неверен только статус, поэтому
        # здесь ничего не начисляется, только возвращается "succeeded"
        # платежам, по которым провайдер прислал успешную оплату (тип 1).
        # Повторный запуск ничего не меняет.
        conn.execute(
            "UPDATE payments SET status = 'succeeded'"
            " WHERE status <> 'succeeded' AND provider_id IN ("
            "   SELECT json_extract(body, '$.dealId') FROM notices"
            "   WHERE json_valid(body)"
            "     AND json_extract(body, '$.notificationType') = 1"
            "     AND json_extract(body, '$.isSuccess') = 1)"
        )

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # ------------------------------------------------------------ пользователи

    def ensure_user(self, user_id: str | None) -> User:
        """Найти пользователя или завести нового."""
        if user_id:
            found = self.user(user_id)
            if found:
                return found
        new_id = user_id or uuid.uuid4().hex
        with self._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO users (id, created_at, free_used) VALUES (?, ?, 0)",
                (new_id, time.time()),
            )
        return self.user(new_id)  # type: ignore[return-value]

    def user(self, user_id: str) -> User | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if not row:
            return None
        return User(
            id=row["id"],
            created_at=row["created_at"],
            free_used=row["free_used"],
            paid_until=row["paid_until"],
            email=row["email"],
            is_admin=bool(row["is_admin"]),
            unlimited=bool(row["unlimited"]),
            note=row["note"] or "",
            credits=row["credits"] or 0,
            balance=row["balance"] or 0.0,
            studio_credits=row["studio_credits"] or 0,
            password_hash=row["password_hash"],
            registered_at=row["registered_at"],
        )

    def add_credits(self, user_id: str, count: int) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE users SET credits = credits + ? WHERE id = ?", (count, user_id)
            )

    def add_studio_credits(self, user_id: str, count: int) -> None:
        with self._connect() as conn:
            conn.execute("UPDATE users SET studio_credits = studio_credits + ? WHERE id = ?",
                         (count, user_id))

    def spend_studio_credit(self, user_id: str) -> bool:
        """Списать одну генерацию из пакета Студии -- атомарно, как spend_credit."""
        with self._connect() as conn:
            changed = conn.execute(
                "UPDATE users SET studio_credits = studio_credits - 1"
                " WHERE id = ? AND studio_credits > 0", (user_id,)).rowcount
        return bool(changed)

    def spend_credit(self, user_id: str) -> bool:
        """
        Списать один оплаченный трек -- и сказать, был ли он.

        Условие на остаток в том же запросе, что и списание, по тем же
        соображениям, что и в spend_balance: два одновременных разбора не
        должны оба увидеть "кредит есть" и списать по одному разу каждый,
        потратив кредит дважды.
        """
        with self._connect() as conn:
            changed = conn.execute(
                "UPDATE users SET credits = credits - 1 WHERE id = ? AND credits > 0",
                (user_id,),
            ).rowcount
        return bool(changed)

    def add_balance(self, user_id: str, amount: float) -> None:
        """Пополнить баланс на произвольную сумму -- ровно ту, что пришла в оплате."""
        with self._connect() as conn:
            conn.execute(
                "UPDATE users SET balance = balance + ? WHERE id = ?", (amount, user_id)
            )

    def spend_balance(self, user_id: str, amount: float) -> bool:
        """
        Списать с баланса ровно за один разбор -- и сказать, хватило ли.

        Условие на текущий остаток в том же запросе, что и списание: два
        одновременных запуска разбора не должны оба увидеть "баланса
        хватает" и оба списать, уведя баланс в минус.
        """
        with self._connect() as conn:
            changed = conn.execute(
                "UPDATE users SET balance = balance - ? WHERE id = ? AND balance >= ?",
                (amount, user_id, amount),
            ).rowcount
        return bool(changed)

    # ------------------------------------------------------ учётные записи

    def user_by_email(self, email: str) -> User | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id FROM users WHERE email = ?", (email,)
            ).fetchone()
        return self.user(row["id"]) if row else None

    def register(self, user_id: str, email: str, password_hash: str) -> None:
        """
        Привязать почту и пароль к существующей записи.

        Именно к существующей, а не к новой: человек уже мог разобрать
        треки анонимно, и при регистрации они должны остаться при нём.
        """
        with self._connect() as conn:
            try:
                conn.execute(
                    "UPDATE users SET email = ?, password_hash = ?, registered_at = ?"
                    " WHERE id = ?",
                    (email, password_hash, time.time(), user_id),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("Такая почта уже зарегистрирована.") from exc

    def set_password(self, user_id: str, password_hash: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE users SET password_hash = ? WHERE id = ?",
                (password_hash, user_id),
            )

    def move_jobs(self, from_user: str, to_user: str) -> int:
        """
        Перенести треки с анонимной записи на учётную.

        Нужно, когда человек разобрал что-то анонимно, а потом вошёл в
        уже существующий аккаунт: терять разобранное нельзя.
        """
        if from_user == to_user:
            return 0
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE jobs SET user_id = ? WHERE user_id = ?", (to_user, from_user)
            )
            return cursor.rowcount

    def delete_user_if_empty(self, user_id: str) -> None:
        """Убрать анонимную запись, с которой всё перенесли."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM jobs WHERE user_id = ?", (user_id,)
            ).fetchone()
            if row["n"] == 0:
                conn.execute(
                    "DELETE FROM users WHERE id = ? AND email IS NULL", (user_id,)
                )

    # ------------------------------------------------------ восстановление

    def create_reset(self, user_id: str, token_hash: str, expires_at: float) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO resets"
                " (token_hash, user_id, created_at, expires_at, used_at)"
                " VALUES (?, ?, ?, ?, NULL)",
                (token_hash, user_id, time.time(), expires_at),
            )

    def consume_reset(self, token_hash: str) -> str | None:
        """
        Проверить и погасить код восстановления.

        Погашение и проверка в одной операции: иначе одним кодом можно
        было бы сменить пароль дважды.
        """
        now = time.time()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT user_id FROM resets"
                " WHERE token_hash = ? AND used_at IS NULL AND expires_at > ?",
                (token_hash, now),
            ).fetchone()
            if not row:
                return None
            conn.execute(
                "UPDATE resets SET used_at = ? WHERE token_hash = ?", (now, token_hash)
            )
            return row["user_id"]

    def purge_resets(self, user_id: str) -> None:
        """Погасить все прежние коды -- например, после смены пароля."""
        with self._connect() as conn:
            conn.execute("DELETE FROM resets WHERE user_id = ?", (user_id,))

    # -------------------------------------------------------- обращения

    # -------------------------------------------------- уведомления об оплате

    def save_notice(self, provider: str, body: dict, accepted: bool,
                    reason: str = "") -> None:
        """
        Сохранить пришедшее уведомление -- принятое и отвергнутое тоже.

        Отвергнутое важнее: именно по нему потом подбирается формула
        подписи. Без записи причина отказа теряется навсегда, и остаётся
        только гадать, почему оплата не доходит.
        """
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO notices (created_at, provider, body, accepted, reason)"
                " VALUES (?, ?, ?, ?, ?)",
                (time.time(), provider, json.dumps(body, ensure_ascii=False),
                 int(accepted), reason[:300]),
            )
            # Держим последние два десятка: это диагностика, а не архив
            conn.execute(
                "DELETE FROM notices WHERE id NOT IN"
                " (SELECT id FROM notices ORDER BY id DESC LIMIT 20)"
            )

    def notices(self, limit: int = 20) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM notices ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            try:
                item["body"] = json.loads(item["body"])
            except ValueError:
                pass
            out.append(item)
        return out

    # ------------------------------------------------------------- приглашения

    def create_invite(self, created_by: str, uses: int = 1, note: str = "") -> str:
        """
        Код приглашения.

        Короткий и без похожих друг на друга знаков: его диктуют в чате и
        набирают руками, а единица с буквой I в этом деле -- source ошибок.
        """
        import secrets

        alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
        code = "".join(secrets.choice(alphabet) for _ in range(8))
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO invites (code, created_at, created_by, uses_left, note)"
                " VALUES (?, ?, ?, ?, ?)",
                (code, time.time(), created_by, max(1, uses), note),
            )
        return code

    def invites(self, created_by: str) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM invites WHERE created_by = ? ORDER BY created_at DESC",
                (created_by,),
            ).fetchall()
        return [dict(r) for r in rows]

    def spend_invite(self, code: str) -> bool:
        """
        Погасить одно использование кода.

        Списание и проверка -- одним запросом с условием uses_left > 0:
        иначе двое, открывших последнюю ссылку одновременно, оба прошли бы.
        """
        with self._connect() as conn:
            changed = conn.execute(
                "UPDATE invites SET uses_left = uses_left - 1, used = used + 1"
                " WHERE code = ? AND uses_left > 0",
                (code.strip().upper(),),
            ).rowcount
        return bool(changed)

    def drop_invite(self, code: str, created_by: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM invites WHERE code = ? AND created_by = ?",
                (code, created_by),
            )

    def create_ticket(self, user_id: str, email: str | None, topic: str, body: str) -> str:
        ticket_id = uuid.uuid4().hex
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO tickets (id, user_id, created_at, email, topic, body)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (ticket_id, user_id, time.time(), email, topic, body),
            )
        return ticket_id

    def tickets(self, status: str | None = None, limit: int = 200) -> list[dict]:
        query = "SELECT * FROM tickets"
        params: tuple = ()
        if status:
            query += " WHERE status = ?"
            params = (status,)
        # Новые сверху: по ним и идёт отсчёт срока ответа
        query += " ORDER BY created_at DESC LIMIT ?"
        with self._connect() as conn:
            rows = conn.execute(query, (*params, limit)).fetchall()
        return [dict(row) for row in rows]

    def ticket(self, ticket_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM tickets WHERE id = ?", (ticket_id,)
            ).fetchone()
        return dict(row) if row else None

    def user_tickets(self, user_id: str) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM tickets WHERE user_id = ? ORDER BY created_at DESC",
                (user_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def answer_ticket(self, ticket_id: str, answer: str, status: str = "answered") -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE tickets SET answer = ?, status = ?, answered_at = ? WHERE id = ?",
                (answer, status, time.time(), ticket_id),
            )

    # ----------------------------------------------------------- удаление

    def delete_job(self, job_id: str) -> list[str]:
        """
        Удалить задание и порождённые им. Возвращает список всех id --
        по ним вызывающий удалит файлы с диска.
        """
        children = [child.id for child in self.child_jobs(job_id)]
        ids = [job_id, *children]
        with self._connect() as conn:
            conn.executemany("DELETE FROM jobs WHERE id = ?", [(i,) for i in ids])
        return ids

    def spend_free(self, user_id: str, limit: int) -> bool:
        """
        Списать одну пробную песню -- и сказать, была ли она.

        Условие на счётчик в том же запросе, что и увеличение -- как в
        spend_balance: без него два одновременных разбора оба видят
        "лимит не исчерпан" и оба списывают пробную песню, отдавая одну
        сверх положенного.
        """
        with self._connect() as conn:
            changed = conn.execute(
                "UPDATE users SET free_used = free_used + 1 WHERE id = ? AND free_used < ?",
                (user_id, limit),
            ).rowcount
        return bool(changed)

    def set_flags(
        self,
        user_id: str,
        *,
        unlimited: bool | None = None,
        is_admin: bool | None = None,
        note: str | None = None,
    ) -> None:
        """Пометки, которые ставит владелец сервиса руками."""
        fields: dict = {}
        if unlimited is not None:
            fields["unlimited"] = int(unlimited)
        if is_admin is not None:
            fields["is_admin"] = int(is_admin)
        if note is not None:
            fields["note"] = note
        if not fields:
            return
        assignments = ", ".join(f"{k} = ?" for k in fields)
        with self._connect() as conn:
            conn.execute(
                f"UPDATE users SET {assignments} WHERE id = ?", (*fields.values(), user_id)
            )

    def all_users(self, limit: int = 500) -> list[User]:
        """Все пользователи -- для консольных команд, свежие сверху."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM users ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [self._user_from_row(r) for r in rows]

    def users_page(self, query: str = "", offset: int = 0, limit: int = 50) -> dict:
        """
        Страница списка пользователей для админки.

        Раньше админка забирала всех разом, причём на каждого делался
        отдельный запрос, а чтобы показать число треков -- подгружались
        сами задания, до пятисот на человека. На десятке пользователей
        это незаметно, на тысяче -- тысячи запросов и полминуты ожидания
        ради одной страницы.

        Теперь всё считает база: счётчик треков берётся одной группировкой,
        а наружу отдаётся ровно одна страница. Порядок тоже осмысленный:
        сначала те, у кого есть доступ или треки, потом остальные --
        владельцу нужны живые люди, а не хвост из случайных заходов.
        """
        like = f"%{query.strip().lower()}%" if query.strip() else None
        where, params = "", []
        if like:
            # Ищем и по заметке: владелец подписывает друзей и
            # тестировщиков словами, а не идентификаторами, и искать
            # потом будет именно по словам.
            where = (" WHERE LOWER(COALESCE(u.email, '')) LIKE ?"
                     " OR LOWER(u.id) LIKE ?"
                     " OR LOWER(COALESCE(u.note, '')) LIKE ?")
            params = [like, like, like]

        with self._connect() as conn:
            total = conn.execute(
                f"SELECT COUNT(*) AS n FROM users u{where}", params
            ).fetchone()["n"]
            rows = conn.execute(
                "SELECT u.*, COALESCE(j.tracks, 0) AS tracks FROM users u"
                " LEFT JOIN (SELECT user_id, COUNT(*) AS tracks FROM jobs"
                "            WHERE json_extract(settings, '$.parent') IS NULL"
                "            GROUP BY user_id) j ON j.user_id = u.id"
                + where
                + " ORDER BY (u.unlimited = 1 OR COALESCE(u.paid_until, 0) > ?) DESC,"
                  " tracks DESC, u.created_at DESC"
                  " LIMIT ? OFFSET ?",
                (*params, time.time(), limit, offset),
            ).fetchall()

        return {
            "total": total,
            "offset": offset,
            "limit": limit,
            "users": [(self._user_from_row(r), r["tracks"]) for r in rows],
        }

    def _user_from_row(self, row) -> User:
        return User(
            id=row["id"],
            created_at=row["created_at"],
            free_used=row["free_used"],
            paid_until=row["paid_until"],
            email=row["email"],
            is_admin=bool(row["is_admin"]),
            unlimited=bool(row["unlimited"]),
            note=row["note"],
            credits=row["credits"],
            balance=row["balance"] if "balance" in row.keys() else 0.0,
            studio_credits=(row["studio_credits"] or 0) if "studio_credits" in row.keys() else 0,
            password_hash=row["password_hash"] if "password_hash" in row.keys() else None,
            registered_at=row["registered_at"] if "registered_at" in row.keys() else None,
        )

    def stats(self) -> dict:
        """Сводка по сервису."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT"
                " (SELECT COUNT(*) FROM users) AS users,"
                " (SELECT COUNT(*) FROM users WHERE unlimited = 1) AS unlimited,"
                " (SELECT COUNT(*) FROM users WHERE paid_until > ?) AS paid,"
                " (SELECT COUNT(*) FROM jobs) AS jobs,"
                " (SELECT COUNT(*) FROM jobs WHERE status = 'error') AS failed,"
                " (SELECT COALESCE(SUM(amount), 0) FROM payments WHERE status = 'succeeded')"
                "   AS revenue,"
                " (SELECT COUNT(*) FROM tickets WHERE status = 'new') AS open_tickets",
                (time.time(),),
            ).fetchone()
        return dict(row)

    def extend_subscription(self, user_id: str, until: float) -> None:
        with self._connect() as conn:
            conn.execute("UPDATE users SET paid_until = ? WHERE id = ?", (until, user_id))

    # ----------------------------------------------------------------- задания

    def create_job(self, user_id: str, filename: str, settings: dict) -> Job:
        job = Job(
            id=uuid.uuid4().hex,
            user_id=user_id,
            created_at=time.time(),
            filename=filename,
            status="queued",
            settings=settings,
        )
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO jobs (id, user_id, created_at, filename, status, settings)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (job.id, user_id, job.created_at, filename, "queued", json.dumps(settings)),
            )
        return job

    def job(self, job_id: str) -> Job | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if not row:
            return None
        return Job(
            id=row["id"],
            user_id=row["user_id"],
            created_at=row["created_at"],
            filename=row["filename"],
            status=row["status"],
            stage=row["stage"] or "",
            progress=float(row["progress"] or 0.0),
            error=row["error"],
            settings=json.loads(row["settings"] or "{}"),
            result=json.loads(row["result"]) if row["result"] else None,
            counted=bool(row["counted"]),
        )

    def update_job(self, job_id: str, **fields) -> None:
        if not fields:
            return
        if "result" in fields and fields["result"] is not None:
            fields["result"] = json.dumps(fields["result"], ensure_ascii=False)
        if "settings" in fields and not isinstance(fields["settings"], str):
            fields["settings"] = json.dumps(fields["settings"] or {}, ensure_ascii=False)
        if "counted" in fields:
            fields["counted"] = int(bool(fields["counted"]))
        assignments = ", ".join(f"{key} = ?" for key in fields)
        with self._connect() as conn:
            conn.execute(
                f"UPDATE jobs SET {assignments} WHERE id = ?",
                (*fields.values(), job_id),
            )

    def child_jobs(self, parent_id: str) -> list[Job]:
        """
        Задания, порождённые разбором: табы по отдельным партиям.

        Связь хранится в настройках, а не отдельной колонкой -- так схема
        остаётся простой, а таких связей у задания всегда ровно одна.
        """
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id FROM jobs WHERE json_extract(settings, '$.parent') = ?"
                " ORDER BY created_at",
                (parent_id,),
            ).fetchall()
        return [j for j in (self.job(r["id"]) for r in rows) if j]

    def root_jobs(self, user_id: str, limit: int = 60) -> list[Job]:
        """Только сами треки, без порождённых ими заданий на табы и без Студии."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id FROM jobs WHERE user_id = ?"
                " AND json_extract(settings, '$.parent') IS NULL"
                " AND json_extract(settings, '$.kind') IS NULL"
                " ORDER BY created_at DESC LIMIT ?",
                (user_id, limit),
            ).fetchall()
        return [j for j in (self.job(r["id"]) for r in rows) if j]

    def unfinished_studio_jobs(self) -> list[Job]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id FROM jobs WHERE json_extract(settings, '$.kind') = 'studio'"
                " AND status IN ('queued', 'running')"
            ).fetchall()
        return [j for j in (self.job(r["id"]) for r in rows) if j]

    def studio_jobs(self, user_id: str, limit: int = 30) -> list[Job]:
        """Задачи Студии: переделки, дописанные партии, разделения на GPU."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id FROM jobs WHERE user_id = ?"
                " AND json_extract(settings, '$.kind') = 'studio'"
                " ORDER BY created_at DESC LIMIT ?",
                (user_id, limit),
            ).fetchall()
        return [j for j in (self.job(r["id"]) for r in rows) if j]

    def user_jobs(self, user_id: str, limit: int = 30) -> list[Job]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id FROM jobs WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
                (user_id, limit),
            ).fetchall()
        return [j for j in (self.job(r["id"]) for r in rows) if j]

    # ---------------------------------------------------------------- платежи

    def create_payment(
        self, user_id: str, amount: float, provider_id: str | None, plan: str = "month"
    ) -> str:
        payment_id = uuid.uuid4().hex
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO payments"
                " (id, user_id, created_at, amount, status, provider_id, plan)"
                " VALUES (?, ?, ?, ?, 'pending', ?, ?)",
                (payment_id, user_id, time.time(), amount, provider_id, plan),
            )
        return payment_id

    def payment_counts(self, since: float) -> dict:
        """Сколько платежей в каком статусе с момента since -- для мониторинга."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT status, plan, COUNT(*) AS n FROM payments"
                " WHERE created_at >= ? GROUP BY status, plan",
                (since,),
            ).fetchall()
        return {f"{row['plan']}/{row['status']}": row["n"] for row in rows}

    def payments_since(self, since: float) -> list[dict]:
        """Платежи всех пользователей с момента since, новые сверху -- для отчёта."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT p.created_at, p.amount, p.status, p.plan, p.commission,"
                "       p.user_id, u.email"
                " FROM payments p LEFT JOIN users u ON u.id = p.user_id"
                " WHERE p.created_at >= ? ORDER BY p.created_at DESC",
                (since,),
            ).fetchall()
        return [dict(row) for row in rows]

    def set_commission(self, payment_id: str, commission: float) -> None:
        with self._connect() as conn:
            conn.execute("UPDATE payments SET commission = ? WHERE id = ?",
                         (commission, payment_id))

    def user_payments(self, user_id: str, limit: int = 20) -> list[dict]:
        """Платежи человека, новые сверху -- чтобы он сам видел, дошли ли деньги."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT created_at, amount, status, plan FROM payments"
                " WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
                (user_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def payment_by_provider(self, provider_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM payments WHERE provider_id = ?", (provider_id,)
            ).fetchone()
        return dict(row) if row else None

    def set_payment_status(self, payment_id: str, status: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE payments SET status = ? WHERE id = ?", (status, payment_id)
            )

    def mark_paid_once(self, payment_id: str) -> bool:
        """
        Пометить платёж оплаченным -- и сказать, случилось ли это ВПЕРВЫЕ.

        Платёжные сервисы повторяют уведомления: если ответ потерялся или
        пришёл не сразу, то же самое придёт ещё раз, и это нормально. А
        вот начислять по нему второй раз -- уже не нормально: за один
        платёж человек получил бы два трека или два месяца подписки.

        Проверка и пометка делаются одним запросом с условием на текущее
        состояние. Разнеси их на два -- и два одновременных уведомления
        оба увидели бы "ещё не оплачен" и оба начислили бы своё.
        """
        with self._connect() as conn:
            changed = conn.execute(
                "UPDATE payments SET status = 'succeeded'"
                " WHERE id = ? AND status <> 'succeeded'",
                (payment_id,),
            ).rowcount
        return bool(changed)
