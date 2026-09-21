#!/usr/bin/env python3
"""
Управление пользователями из консоли.

Замена привычному createsuperuser из Django. Нужна, когда до веб-админки
не добраться: забыт ключ, не работает сеть, надо разобраться в проблеме
прямо на сервере.

Запуск -- ОБЯЗАТЕЛЬНО интерпретатором из venv, иначе не найдутся
зависимости:

    /opt/nasluh/venv/bin/python /opt/nasluh/app/deploy/admin.py список
    /opt/nasluh/venv/bin/python /opt/nasluh/app/deploy/admin.py админ ivan@mail.ru

Команда "админ" заведёт учётную запись, если её ещё нет, и спросит пароль:
в первый день записей в базе нет вообще, а войти в управление уже нужно.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from web import auth, billing  # noqa: E402
from web.storage import Storage  # noqa: E402

DATA_DIR = os.environ.get("MIDI2TAB_DATA", "/opt/nasluh/data")


def store() -> Storage:
    path = os.path.join(DATA_DIR, "app.db")
    if not os.path.isfile(path):
        sys.exit(
            f"База не найдена: {path}\n"
            "Укажите папку данных: MIDI2TAB_DATA=/путь ... admin.py"
        )
    return Storage(path)


def when(moment: float | None) -> str:
    return time.strftime("%d.%m.%Y", time.localtime(moment)) if moment else "—"


def find(db: Storage, who: str, create: bool = False):
    """
    Найти по почте или по началу идентификатора.

    С create=True недостающая запись заводится на месте. Это нужно как
    раз в первый день: сайт ещё никто не открывал, учётных записей в базе
    нет, а владельцу уже надо войти в управление. Иначе получался замкнутый
    круг -- права выдаются только существующему, а существующим становишься
    только через сайт.
    """
    user = db.user_by_email(normalise(who)) if "@" in who else None
    if user:
        return user
    for candidate in db.all_users():
        if candidate.id.startswith(who):
            return candidate
    if create and "@" in who:
        return make_user(db, who)
    sys.exit(
        f"Пользователь не найден: {who}\n"
        + ("Заведите запись: admin.py создать " + who if "@" in who else
           "Укажите почту или начало идентификатора; список -- admin.py список")
    )


def normalise(email: str) -> str:
    return auth.normalise_email(email)


def make_user(db: Storage, email: str, password: str | None = None):
    """Завести учётную запись прямо с сервера."""
    import getpass

    email = normalise(email)
    if db.user_by_email(email):
        sys.exit(f"Почта уже занята: {email}")
    if not password:
        password = getpass.getpass(f"Пароль для {email}: ")
        again = getpass.getpass("Ещё раз: ")
        if password != again:
            sys.exit("Пароли не совпали.")
    try:
        hashed = auth.hash_password(password)
    except auth.AuthError as exc:
        sys.exit(str(exc))
    user = db.ensure_user(None)
    try:
        db.register(user.id, email, hashed)
    except ValueError as exc:
        sys.exit(str(exc))
    print(f"Создана учётная запись: {email}")
    return db.user(user.id)


# ------------------------------------------------------------------ команды


def cmd_list(db: Storage, args) -> None:
    users = db.all_users()
    if not users:
        print("Пользователей пока нет.")
        return
    print(f"{'id':10} {'почта':28} {'треков':>7} {'проб':>5}  доступ")
    print("-" * 74)
    for user in users:
        marks = []
        if user.is_admin:
            marks.append("админ")
        if user.unlimited:
            marks.append("безлимит")
        elif user.subscribed:
            marks.append(f"подписка до {when(user.paid_until)}")
        elif user.credits:
            marks.append(f"оплачено {user.credits}")
        print(
            f"{user.id[:8]:10} {(user.email or '(анонимный)'):28} "
            f"{len(db.root_jobs(user.id, 500)):>7} {user.free_used:>5}  "
            f"{', '.join(marks) or '—'}"
        )
    stats = db.stats()
    print(
        f"\nВсего: {stats['users']}, с подпиской {stats['paid']}, "
        f"безлимит {stats['unlimited']}, обращений ждёт {stats['open_tickets']}"
    )


def cmd_create(db: Storage, args) -> None:
    user = make_user(db, args.who, args.password)
    print(f"Идентификатор: {user.id[:8]}. Войти можно на странице /account.")


def cmd_admin(db: Storage, args) -> None:
    # Заводим на месте, если записи ещё нет: в первый день её и не будет.
    user = find(db, args.who, create=True)
    db.set_flags(user.id, is_admin=True, unlimited=True, note=args.note or "владелец")
    print(
        f"{user.email or user.id[:8]}: выданы права администратора и безлимит.\n"
        "Войдите на /account этой почтой, дальше откроется /admin."
    )


def cmd_unlimited(db: Storage, args) -> None:
    user = find(db, args.who)
    db.set_flags(user.id, unlimited=not args.off, note=args.note)
    print(
        f"{user.email or user.id[:8]}: безлимит "
        f"{'снят' if args.off else 'выдан'}."
    )


def cmd_grant(db: Storage, args) -> None:
    user = find(db, args.who)
    until = billing.grant_subscription(db, user.id, days=args.days)
    print(f"{user.email or user.id[:8]}: подписка до {when(until)}.")


def cmd_password(db: Storage, args) -> None:
    """Задать пароль вручную -- если человек не может восстановить сам."""
    user = find(db, args.who)
    if not user.email:
        sys.exit("У этой записи нет почты: сначала должна пройти регистрация.")
    import getpass

    password = args.password or getpass.getpass("Новый пароль: ")
    try:
        db.set_password(user.id, auth.hash_password(password))
    except auth.AuthError as exc:
        sys.exit(str(exc))
    db.purge_resets(user.id)
    print(f"{user.email}: пароль изменён, прежние коды восстановления погашены.")


def cmd_tickets(db: Storage, args) -> None:
    from web import support

    rows = db.tickets("new" if args.new else None)
    if not rows:
        print("Обращений нет.")
        return
    for row in rows:
        level = support.urgency(row["created_at"], row["topic"], row["answered_at"])
        mark = (
            f"ПРОСРОЧЕНО на {abs(level.days_left):.0f} дн"
            if level.overdue
            else f"осталось {level.days_left:.0f} дн"
        )
        print(f"\n[{row['status']}] {support.topic_title(row['topic'])} — {mark}")
        print(f"  от {row['email'] or row['user_id'][:8]}, {when(row['created_at'])}")
        print(f"  {row['body'][:300]}")
        if row["answer"]:
            print(f"  ответ: {row['answer'][:200]}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Управление пользователями NASLUX",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("список", help="показать всех пользователей").set_defaults(
        func=cmd_list
    )

    p_create = sub.add_parser("создать", help="завести учётную запись")
    p_create.add_argument("who", help="почта")
    p_create.add_argument("--password", default=None, help="без него спросит скрытно")
    p_create.set_defaults(func=cmd_create)

    p_admin = sub.add_parser(
        "админ", help="выдать права администратора и безлимит (заведёт запись, если её нет)"
    )
    p_admin.add_argument("who", help="почта или начало идентификатора")
    p_admin.add_argument("--note", default="", help="заметка")
    p_admin.set_defaults(func=cmd_admin)

    p_unlim = sub.add_parser("безлимит", help="выдать или снять безлимит")
    p_unlim.add_argument("who")
    p_unlim.add_argument("--off", action="store_true", help="снять")
    p_unlim.add_argument("--note", default=None)
    p_unlim.set_defaults(func=cmd_unlimited)

    p_grant = sub.add_parser("подписка", help="продлить подписку")
    p_grant.add_argument("who")
    p_grant.add_argument("--days", type=int, default=30)
    p_grant.set_defaults(func=cmd_grant)

    p_pass = sub.add_parser("пароль", help="задать пароль вручную")
    p_pass.add_argument("who")
    p_pass.add_argument("--password", default=None, help="без него спросит скрытно")
    p_pass.set_defaults(func=cmd_password)

    p_tick = sub.add_parser("обращения", help="показать обращения")
    p_tick.add_argument("--new", action="store_true", help="только неотвеченные")
    p_tick.set_defaults(func=cmd_tickets)

    args = parser.parse_args()
    args.func(store(), args)


if __name__ == "__main__":
    main()
