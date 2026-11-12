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


def cmd_payment(db: Storage, args) -> None:
    """Что видит служба о приёме оплаты."""
    gateway = billing.provider()
    print("Приём оплаты\n" + "-" * 44)
    for key, value in gateway.diagnose().items():
        print(f"  {key:28} {value}")
    if not gateway.configured():
        print(
            "\nПеременные читаются из /opt/nasluh/nasluh.env.\n"
            "После правки обязателен перезапуск: systemctl restart nasluh\n"
            "Проверить, что их видит именно служба:\n"
            "  systemctl show nasluh -p Environment\n"
            "  tr '\\0' '\\n' < /proc/$(pgrep -f 'uvicorn web.app' | head -1)/environ"
            " | grep GETPLATINUM"
        )


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


# --- перенос клиентов из выгрузки GetPlatinum ------------------------------
# После сбоя Beget (02.11.2026) сайт переехал на новый сервер с пустой базой.
# Клиенты есть в кабинете GetPlatinum: ФИО, почта, телефон, сумма покупок.
# «перенос» заводит им учётные записи, кладёт оплаченное на баланс и пишет
# письмо со ссылкой «задать пароль» (неделя). Без --apply только показывает
# план. Повторный запуск безопасен: у кого запись уже есть -- пропускает,
# дважды деньги не начисляет. Когда вернётся архив старой базы
# (restore_data.sh), он заменит всё целиком -- с настоящими балансами.
MIGRATE_MARK = "перенос:getplatinum"
MIGRATE_LINK_TTL = 7 * 24 * 3600


def read_clients(path: str) -> list[dict]:
    """Строки выгрузки -> клиенты по почте (несколько покупок -- одна запись)."""
    import csv
    import re

    raw = Path(path).read_text(encoding="utf-8-sig")
    dialect = csv.Sniffer().sniff(raw.splitlines()[0], delimiters=";,\t")
    rows = list(csv.reader(raw.splitlines(), dialect))
    head = [h.strip().lower() for h in rows[0]]

    def col(*names):
        for i, h in enumerate(head):
            if any(n in h for n in names):
                return i
        return None

    i_mail, i_name = col("mail", "почт"), col("фио", "имя", "name")
    i_phone, i_sum = col("телефон", "phone"), col("сумм", "amount", "sum")
    if i_mail is None:
        sys.exit("В таблице нет столбца с почтой (E-mail)")
    clients: dict[str, dict] = {}
    for row in rows[1:]:
        if len(row) <= i_mail or "@" not in row[i_mail]:
            continue
        try:
            email = normalise(row[i_mail].strip())
        except auth.AuthError:
            print(f"  пропуск: странная почта {row[i_mail]!r}")
            continue
        got = clients.setdefault(email, {"email": email, "name": "", "phone": "", "paid": 0.0})
        name = row[i_name].strip() if i_name is not None and len(row) > i_name else ""
        if len(name) > len(got["name"]) and "провер" not in name.lower():
            got["name"] = name
        if i_phone is not None and len(row) > i_phone and not got["phone"]:
            got["phone"] = row[i_phone].strip()
        if i_sum is not None and len(row) > i_sum:
            amount = re.sub(r"[^0-9,.]", "", row[i_sum]).replace(",", ".")
            got["paid"] += float(amount) if amount.replace(".", "", 1).isdigit() else 0.0
    return list(clients.values())


def welcome_letter(client: dict, link: str) -> str:
    hello = f"Здравствуйте, {client['name'].split()[0]}!" if client["name"] else "Здравствуйте!"
    money = (f"Оплаченные вами {client['paid']:.0f} ₽ уже на балансе.\n" if client["paid"] else "")
    return (
        f"{hello}\n\n"
        "2 ноября у нашего хостинг-провайдера произошла крупная авария, и NASLUX "
        "переехал на новый сервер. Ваша учётная запись перенесена.\n"
        f"{money}\n"
        "Чтобы войти, задайте пароль по ссылке (действует 7 дней):\n\n"
        f"{link}\n\n"
        "Если ссылка устарела -- откройте naslux.ru/account и нажмите «Забыли пароль?» "
        "с этой почтой. Вход через Яндекс и VK тоже работает.\n\n"
        "Прежние треки вернутся, как только провайдер отдаст резервную копию -- "
        "мы напишем. Простите за неудобства!\n\n"
        "Команда NASLUX\n"
    )


def cmd_migrate(db: Storage, args) -> None:
    import secrets as pysecrets

    clients = read_clients(args.file)
    skip = {normalise(x) for x in args.skip}
    site = os.environ.get("NASLUX_SITE_URL", "https://naslux.ru").rstrip("/")
    if args.send:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from web import mailer

        ok, why = mailer.available()
        if not ok:
            sys.exit(f"Почта не настроена: {why}")
    print(f"{'ПЕРЕНОС' if args.apply else 'ПЛАН (ничего не меняю; запустите с --apply)'}: {len(clients)} клиентов")
    for c in clients:
        line = f"  {c['email']:<32} {c['name'] or '—':<28} {c['phone'] or '—':<14} {c['paid']:>8.2f} ₽"
        if c["email"] in skip:
            print(line + "  → пропуск (--skip)")
            continue
        if db.user_by_email(c["email"]):
            print(line + "  → уже есть, не трогаю")
            continue
        credit = c["paid"] if args.balance else 0.0
        mailable = not c["email"].endswith("@vk.id")
        plan = f"  → завести{f', +{credit:.0f} ₽' if credit else ''}{', письмо' if args.send and mailable else ''}"
        print(line + plan)
        if not args.apply:
            continue
        user = db.ensure_user(None)
        # пароль никто не знает: человек задаст свой по ссылке из письма
        db.register(user.id, c["email"], auth.hash_password(pysecrets.token_urlsafe(24)))
        note = " · ".join(x for x in (c["name"], c["phone"], f"оплачено {c['paid']:.0f} ₽", MIGRATE_MARK) if x)
        db.set_flags(user.id, note=note)
        if credit:
            db.add_balance(user.id, credit)
        if args.send and mailable:
            token, token_hash = auth.make_reset_token()
            db.create_reset(user.id, token_hash, auth.token_expiry(MIGRATE_LINK_TTL))
            try:
                mailer.send(c["email"], "NASLUX переехал -- ваш аккаунт сохранён",
                            welcome_letter(c, f"{site}/account?reset={token}"))
                print("      письмо отправлено")
            except Exception as exc:  # noqa: BLE001
                print(f"      письмо НЕ ушло: {exc}")


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

    sub.add_parser("оплата", help="почему не работает приём оплаты").set_defaults(
        func=cmd_payment
    )

    p_tick = sub.add_parser("обращения", help="показать обращения")
    p_tick.add_argument("--new", action="store_true", help="только неотвеченные")
    p_tick.set_defaults(func=cmd_tickets)

    p_mig = sub.add_parser("перенос", help="завести клиентов из выгрузки GetPlatinum (CSV)")
    p_mig.add_argument("file", help="CSV: ФИО, E-mail, Телефон, Сумма покупок")
    p_mig.add_argument("--apply", action="store_true", help="выполнить (без него -- только план)")
    p_mig.add_argument("--send", action="store_true", help="разослать письма «задать пароль»")
    p_mig.add_argument("--no-balance", dest="balance", action="store_false",
                       help="не класть оплаченное на баланс")
    p_mig.add_argument("--skip", nargs="*", default=[], help="почты, которые не переносить (свои, тестовые)")
    p_mig.set_defaults(func=cmd_migrate)

    args = parser.parse_args()
    args.func(store(), args)


if __name__ == "__main__":
    main()
