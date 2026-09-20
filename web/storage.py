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
    email        TEXT
);

CREATE TABLE IF NOT EXISTS jobs (
    id           TEXT PRIMARY KEY,
    user_id      TEXT NOT NULL,
    created_at   REAL NOT NULL,
    filename     TEXT NOT NULL,
    status       TEXT NOT NULL,
    stage        TEXT NOT NULL DEFAULT '',
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
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE INDEX IF NOT EXISTS jobs_user ON jobs(user_id, created_at);
"""


@dataclass
class User:
    id: str
    created_at: float
    free_used: int
    paid_until: float | None
    email: str | None = None

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
        )

    def spend_free(self, user_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE users SET free_used = free_used + 1 WHERE id = ?", (user_id,)
            )

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
        """Только сами треки, без порождённых ими заданий на табы."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id FROM jobs WHERE user_id = ?"
                " AND json_extract(settings, '$.parent') IS NULL"
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

    def create_payment(self, user_id: str, amount: float, provider_id: str | None) -> str:
        payment_id = uuid.uuid4().hex
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO payments (id, user_id, created_at, amount, status, provider_id)"
                " VALUES (?, ?, ?, ?, 'pending', ?)",
                (payment_id, user_id, time.time(), amount, provider_id),
            )
        return payment_id

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
