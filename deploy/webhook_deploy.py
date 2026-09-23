#!/usr/bin/env python3
"""
Отдельный слушатель вебхука GitHub: получил пуш в нужную ветку -- запустил
deploy/update.sh.

Намеренно НЕ часть web/app.py и не использует venv приложения. Служба
nasluh (см. nasluh.service) работает от отдельного непривилегированного
пользователя с ProtectSystem=strict и ReadWritePaths только на данные --
она физически не может ни переписать код в /opt/nasluh/app, ни
перезапустить себя через systemctl. Обновление требует root (chown,
systemctl restart), а та же самая программа, что разбирает чужой ввод
(платёжные вебхуки, загруженные песни), не должна параллельно уметь
исполнять код от root -- одна дыра в вебе стала бы дырой во всём сервере.
Поэтому это отдельный процесс, отдельный systemd-юнит, отдельный секрет
(deploy-webhook.env, не nasluh.env), только стандартная библиотека --
минимальная и легко проверяемая поверхность атаки.

Слушает ТОЛЬКО 127.0.0.1 -- наружу его пускает nginx одним конкретным
путём (/internal/deploy-webhook), см. nginx.conf. Прямого доступа из
интернета к этому порту нет.

Настройка -- в deploy/README.md.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SECRET = os.environ.get("DEPLOY_WEBHOOK_SECRET", "")
BRANCH = os.environ.get("DEPLOY_WEBHOOK_BRANCH", "claude/epic-mccarthy-sl30hz")
PORT = int(os.environ.get("DEPLOY_WEBHOOK_PORT", "8099"))
UPDATE_SCRIPT = os.environ.get(
    "DEPLOY_UPDATE_SCRIPT", "/opt/nasluh/app/deploy/update.sh"
)

_running: subprocess.Popen | None = None


def verify_signature(secret: str, body: bytes, header: str | None) -> bool:
    """
    Проверить X-Hub-Signature-256 от GitHub.

    Сравнение постоянного времени (hmac.compare_digest): иначе по
    скорости ответа можно было бы подобрать подпись по одному байту.
    """
    if not secret or not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    got = header[len("sha256="):]
    return hmac.compare_digest(expected, got)


def should_deploy(event: str | None, payload: dict, branch: str = BRANCH) -> tuple[bool, str]:
    """
    Решить, разворачивать ли этот вебхук. Возвращает (да/нет, причина).

    GitHub шлёт событие "ping" сразу при добавлении вебхука -- на него
    отвечаем без разворачивания, иначе сама настройка вебхука уже
    вызвала бы деплой. "push" с чужой веткой или удалением ветки --
    тоже не повод.
    """
    if event == "ping":
        return False, "ping -- вебхук просто проверяют, разворачивать нечего"
    if event != "push":
        return False, f"событие {event!r} -- не push, пропускаем"
    if payload.get("deleted"):
        return False, "это удаление ветки, а не пуш кода"
    ref = payload.get("ref", "")
    wanted = f"refs/heads/{branch}"
    if ref != wanted:
        return False, f"ветка {ref!r} -- ждём {wanted!r}, пропускаем"
    return True, "разворачиваю"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: object) -> None:
        sys.stderr.write(f"{self.address_string()} {fmt % args}\n")

    def do_POST(self) -> None:  # noqa: N802 -- имя задано BaseHTTPRequestHandler
        global _running

        length = int(self.headers.get("Content-Length", "0") or "0")
        body = self.rfile.read(length) if length else b""

        if not SECRET:
            self._respond(503, "DEPLOY_WEBHOOK_SECRET не задан")
            return
        signature = self.headers.get("X-Hub-Signature-256")
        if not verify_signature(SECRET, body, signature):
            # В журнал -- то, по чему видно причину, но не сам секрет:
            # нет заголовка -- в GitHub не задан Secret; есть, а не
            # сходится -- в GitHub вставлено не то значение (частая ошибка:
            # вся строка "DEPLOY_WEBHOOK_SECRET=..." вместе с префиксом).
            print(f"401: заголовок подписи {'есть' if signature else 'НЕТ'}, "
                  f"тело {len(body)} байт, секрет на сервере {len(SECRET)} символов")
            self._respond(401, "подпись не совпала")
            return

        try:
            payload = json.loads(body or b"{}")
        except ValueError:
            self._respond(400, "тело не JSON")
            return

        deploy, reason = should_deploy(self.headers.get("X-GitHub-Event"), payload)
        print(reason)
        if not deploy:
            self._respond(200, reason)
            return

        if _running is not None and _running.poll() is None:
            # Не ставим в очередь: следующий пуш всё равно вызовет свой
            # вебхук, а update.sh делает `reset --hard origin/branch` --
            # он и так заберёт всё, что накопилось к тому моменту.
            self._respond(202, "деплой уже идёт, этот пуш заберёт следующий")
            return

        _running = subprocess.Popen(["bash", UPDATE_SCRIPT])
        self._respond(202, "разворачиваю в фоне")

    def _respond(self, code: int, message: str) -> None:
        body = json.dumps({"message": message}, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    if not SECRET:
        sys.exit("DEPLOY_WEBHOOK_SECRET не задан -- слушатель отказывается стартовать")
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Слушаю вебхук деплоя на 127.0.0.1:{PORT}, ветка {BRANCH}")
    server.serve_forever()


if __name__ == "__main__":
    main()
