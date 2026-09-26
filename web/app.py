"""
Веб-приложение: загрузка песни, обработка, плеер с бегущими аккордами.

Запуск:
    uvicorn web.app:app --host 0.0.0.0 --port 8000

Опознание пользователя -- подписанная кука. Это сознательно простой
вариант для старта: заводить почту и пароли до первых платящих
пользователей смысла нет, а подписать куку достаточно, чтобы счётчик
бесплатных песен нельзя было обнулить правкой в браузере.
"""

from __future__ import annotations

import os
import re
import shutil
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from itsdangerous import BadSignature, URLSafeSerializer

from midi2tab import audiochords, audioin, lyrics as lyrics_mod, separate
from midi2tab.timing import GRIDS
from midi2tab.tuning import TUNINGS

from . import auth, billing, mailer, studio, support
from . import jobs as jobs_module
from .jobs import JobRunner
from .storage import Storage

DATA_DIR = os.environ.get("MIDI2TAB_DATA", "data")
SECRET = os.environ.get("MIDI2TAB_SECRET", "")
MAX_UPLOAD_MB = int(os.environ.get("MIDI2TAB_MAX_MB", "60"))
# Ключ владельца. Первый, кто войдёт с ним, получает права администратора
# и безлимит. Без ключа админка недоступна вообще -- это безопаснее, чем
# пароль по умолчанию, который забывают сменить.
ADMIN_KEY = os.environ.get("MIDI2TAB_ADMIN_KEY", "")
# Отдельный токен для автоматических проверок (/api/health) -- НЕ ADMIN_KEY:
# тому, кто снаружи раз в час проверяет, жива ли оплата, не нужны права
# менять пользователей. Держать один секрет на обе задачи значило бы, что
# утечка мониторинга -- это утечка полной админки.
HEALTH_TOKEN = os.environ.get("MIDI2TAB_HEALTH_TOKEN", "")
ALLOWED = (".wav", ".mp3", ".flac", ".ogg", ".m4a", ".aiff", ".aif", ".mid", ".midi")

STATIC_DIR = Path(__file__).parent / "static"


def safe_stem(filename: str) -> str:
    """
    Имя файла без пути и опасных символов.

    Нужно и для безопасности (в имени может прийти ../), и по делу:
    от него зависит, как будут называться скачиваемые .gp5 и .txt.
    """
    stem = Path(filename or "").stem.strip() or "song"
    cleaned = "".join(c for c in stem if c.isalnum() or c in " _-()[]").strip()
    return (cleaned or "song")[:60]

def build_stamp() -> str:
    """
    Отпечаток выложенной версии статики.

    Нужен против самой коварной поломки при обновлении: браузер держит
    в кеше СТАРЫЙ player.js, а страницу получает новую. Старый скрипт
    обращается к элементам, которых в новой странице уже нет, падает --
    и человек видит пустую ленту без аккордов и мёртвую кнопку "играть".
    Ошибка выглядит как сломанный сервер, а на самом деле сломан кеш.

    Отпечаток берётся из времени правки файлов и подставляется в адреса
    css и js: поменялся файл -- поменялся адрес -- браузер обязан скачать
    заново, и смешать старое с новым уже нельзя.
    """
    newest = 0.0
    for path in STATIC_DIR.rglob("*"):
        if path.suffix in (".js", ".css", ".svg"):
            try:
                newest = max(newest, path.stat().st_mtime)
            except OSError:
                pass
    return format(int(newest), "x")


STAMP = build_stamp()


def page(name: str, values: dict | None = None) -> HTMLResponse:
    """Отдать страницу, проставив отпечаток версии в ссылки на статику."""
    html = (STATIC_DIR / name).read_text(encoding="utf-8")
    for key, value in (values or {}).items():
        html = html.replace("{{" + key + "}}", str(value))
    html = re.sub(r'(/static/[\w./-]+\.(?:js|css|svg))"', rf'\1?v={STAMP}"', html)
    return HTMLResponse(
        html,
        # Саму страницу кешировать нельзя: в ней и лежит отпечаток, по
        # которому браузер узнаёт, что статика обновилась.
        headers={"Cache-Control": "no-cache, must-revalidate"},
    )


def download_name(title: str, part: str, suffix: str) -> str:
    """
    Имя скачиваемого файла: по нему должно быть понятно, что внутри.

    Demucs называет свои дорожки guitar.wav и bass.wav, и после трёх
    разобранных песен в папке «Загрузки» лежат три одинаковых guitar.wav.
    Поэтому имя собирается из названия трека и партии.
    """
    base = Path(title or "track").stem.strip() or "track"
    name = f"{base} — {part}" if part else base
    banned = '<>:"/\\|?*'
    cleaned = "".join("_" if ch in banned or ord(ch) < 32 else ch for ch in name)
    return cleaned[:120].strip() + suffix


def _running_port() -> str:
    """
    Порт, на котором нас запустили.

    Берётся из аргументов uvicorn, иначе из переменной PORT. Печатать
    число наугад нельзя: неверная подсказка хуже её отсутствия.
    """
    argv = sys.argv
    for i, arg in enumerate(argv):
        if arg == "--port" and i + 1 < len(argv):
            return argv[i + 1]
        if arg.startswith("--port="):
            return arg.split("=", 1)[1]
    return os.environ.get("PORT", "8000")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # uvicorn печатает "running on http://0.0.0.0:8000", и это сбивает с
    # толку: 0.0.0.0 означает "слушать на всех интерфейсах", открыть такой
    # адрес в браузере нельзя -- он ответит ERR_ADDRESS_INVALID.
    # Прогреваем numba внутри librosa заранее: иначе первый пользователь
    # ждёт в разы дольше остальных, пока компилируются функции.
    audiochords.prewarm()
    resumed = studio_runner.resume()
    if resumed:
        print(f"  Студия: продолжаем следить за задачами — {resumed}")
    port = _running_port()
    print()
    print("  NASLUX запущен. Откройте в браузере:")
    print(f"      http://localhost:{port}")
    if not os.environ.get("MIDI2TAB_SECRET"):
        print()
        print("  Совет: задайте MIDI2TAB_SECRET, иначе при перезапуске")
        print("  сбросится счётчик бесплатных песен у пользователей.")
    print()
    yield
    runner.shutdown()
    studio_runner.shutdown()


storage = Storage(os.path.join(DATA_DIR, "app.db"))
runner = JobRunner(storage, DATA_DIR)
studio_runner = studio.StudioRunner(storage, DATA_DIR)
app = FastAPI(title="NASLUX", lifespan=lifespan)

if not SECRET:
    # Свой ключ на каждый запуск: куки протухнут при перезапуске, но
    # молча подставлять предсказуемый ключ опаснее.
    SECRET = os.urandom(32).hex()
    print("ВНИМАНИЕ: MIDI2TAB_SECRET не задан, использован временный ключ.")
signer = URLSafeSerializer(SECRET, salt="uid")
login_limiter = auth.AttemptLimiter()


# ------------------------------------------------------------- пользователь

def current_user(request: Request):
    raw = request.cookies.get("uid")
    user_id = None
    if raw:
        try:
            user_id = signer.loads(raw)
        except BadSignature:
            user_id = None
    return storage.ensure_user(user_id)


def attach_cookie(response: Response, user_id: str) -> None:
    response.set_cookie(
        "uid",
        signer.dumps(user_id),
        max_age=365 * 24 * 3600,
        httponly=True,
        samesite="lax",
    )


# ------------------------------------------------------------------ страницы

@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    return page("index.html")


def require_admin(request: Request):
    """Пускать в админку только помеченных администраторами."""
    user = current_user(request)
    if not user.is_admin:
        raise HTTPException(403, "Нужны права администратора")
    return user


@app.get("/admin", response_class=HTMLResponse)
def admin_page() -> HTMLResponse:
    return page("admin.html")


@app.post("/api/admin/login")
def api_admin_login(request: Request, key: str = Form(...)):
    """
    Войти как владелец по ключу из переменной окружения.

    Ключ сравнивается посимвольно-постоянным сравнением, чтобы по времени
    ответа нельзя было подбирать его по одному знаку.
    """
    import secrets

    if not ADMIN_KEY:
        raise HTTPException(503, "Админка выключена: не задан MIDI2TAB_ADMIN_KEY")
    # Сравниваем байты, а не строки: secrets.compare_digest на строках
    # требует чистого ASCII и падает с TypeError на кириллическом ключе --
    # вместо входа владелец получал бы пятисотую ошибку.
    if not secrets.compare_digest(key.encode(), ADMIN_KEY.encode()):
        raise HTTPException(403, "Неверный ключ")

    user = current_user(request)
    # Права выдаются УЧЁТНОЙ ЗАПИСИ, а не куке. Без этого владелец,
    # зашедший с другого устройства или почистивший куки, оказывался новым
    # безымянным посетителем -- и выглядело это как "меня выкинуло из
    # админки, а пароля я не знаю". Теперь права переживают смену браузера.
    if not user.registered:
        raise HTTPException(
            403,
            "Сначала заведите учётную запись на странице «Вход» — иначе "
            "права привяжутся к этому браузеру и пропадут вместе с куками.",
        )
    storage.set_flags(user.id, is_admin=True, unlimited=True, note=user.note or "владелец")
    response = JSONResponse({"ok": True})
    attach_cookie(response, user.id)
    return response


def deployed_commit() -> str:
    """
    Какой коммит сейчас работает на сервере.

    Без этого не проверить, доехал ли пуш: автодеплой может молча не
    сработать (так и было -- вебхук отклонялся с 401), и сайт продолжал
    работать на старом коде. Читается прямо из .git, без вызова git:
    служба работает в режиме только-чтение, а файлы читать ей можно.
    """
    git = Path(__file__).resolve().parent.parent / ".git"
    try:
        head = (git / "HEAD").read_text().strip()
        if not head.startswith("ref: "):
            return head[:7]
        ref = head[5:]
        if (git / ref).is_file():
            return (git / ref).read_text().strip()[:7]
        for line in (git / "packed-refs").read_text().splitlines():
            if line.endswith(" " + ref):
                return line.split()[0][:7]
    except OSError:
        pass
    return ""


def require_health_token(request: Request) -> None:
    """Токен из заголовка -- посимвольно-постоянным сравнением, как ADMIN_KEY."""
    import secrets

    if not HEALTH_TOKEN:
        raise HTTPException(503, "Проверки выключены: не задан MIDI2TAB_HEALTH_TOKEN")
    auth = request.headers.get("Authorization", "")
    got = auth[len("Bearer "):] if auth.startswith("Bearer ") else ""
    if not secrets.compare_digest(got.encode(), HEALTH_TOKEN.encode()):
        raise HTTPException(403, "Неверный токен")


@app.get("/api/health")
def api_health(request: Request):
    """
    Узкая, только для чтения проверка для автоматического мониторинга.

    Отдельная от /api/admin/*: тем даёт войти пароль владельца и кука
    браузера, а этой -- только заголовок Authorization, чтобы дёргать её
    скриптом раз в несколько часов, не заводя для этого сессию в браузере.
    """
    require_health_token(request)
    gateway = billing.provider()
    recent = storage.notices(limit=20)
    failed_recent = sum(1 for n in recent if not n["accepted"])
    return {
        "оплата": gateway.diagnose(),
        "уведомлений_за_последние": len(recent),
        "из_них_отклонено": failed_recent,
        # Только время и причина, без тела: в теле бывают почта и номера
        # платежей, а по причине и так видно, что чинить -- подпись,
        # ненайденный платёж или ошибку при создании.
        "отказы": [{"когда": n["created_at"], "причина": n["reason"]}
                   for n in recent if not n["accepted"]],
        # Что сообщил провайдер по каждому уведомлению -- тип и исход, без
        # почты и прочего из тела: по этому видно, приходил ли вообще
        # сигнал "оплачено" или банк отказал.
        "уведомления": [
            {"когда": n["created_at"], "принято": bool(n["accepted"]),
             "тип": n["body"].get("notificationType") if isinstance(n["body"], dict) else None,
             "оплачено": n["body"].get("isSuccess") if isinstance(n["body"], dict) else None,
             "итог": n["reason"]}
            for n in recent if not (isinstance(n["body"], dict) and "попытка оплаты" in n["body"])
        ],
        "платежи_за_неделю": storage.payment_counts(time.time() - 7 * 86400),
        "время": time.time(),
        "версия": deployed_commit(),
    }


@app.get("/api/admin/payment")
def api_admin_payment(request: Request):
    """
    Почему оплата не работает -- без гаданий.

    Самая частая причина не в коде: переменные не доехали до службы,
    ключ не заменили на настоящий, служба не перезапущена. Показываем
    ровно то, что видит процесс.
    """
    require_admin(request)
    gateway = billing.provider()
    return {"состояние": gateway.diagnose()}


FINANCE_GOAL_RUB = 500_000


@app.get("/api/admin/finance")
def api_admin_finance(request: Request, month: str = ""):
    """
    Финансовый отчёт: выручка по месяцам, прошедшие и не прошедшие
    платежи, комиссия и прогноз месяца против цели.

    Месяцы считаются по московскому времени: платёж в 01:00 первого числа
    по Москве относится к новому месяцу, хотя на сервере (UTC) это ещё
    старый. Деньги считаются по дате создания платежа.
    """
    import calendar
    from datetime import datetime
    from zoneinfo import ZoneInfo

    require_admin(request)
    tz = ZoneInfo("Europe/Moscow")
    now = datetime.now(tz)

    keys = []
    year, mon = now.year, now.month
    for _ in range(12):
        keys.append(f"{year:04d}-{mon:02d}")
        year, mon = (year - 1, 12) if mon == 1 else (year, mon - 1)
    keys.reverse()
    since = datetime(int(keys[0][:4]), int(keys[0][5:]), 1, tzinfo=tz).timestamp()

    months = {k: {"month": k, "revenue": 0.0, "commission": 0.0,
                  "paid": 0, "failed": 0, "pending": 0} for k in keys}
    selected = month if month in months else keys[-1]
    rows, payers, by_plan = [], set(), {}
    for p in storage.payments_since(since):
        key = datetime.fromtimestamp(p["created_at"], tz).strftime("%Y-%m")
        bucket = months.get(key)
        if bucket is None:
            continue
        paid = p["status"] == "succeeded"
        if paid:
            bucket["revenue"] += p["amount"]
            bucket["commission"] += p["commission"] or 0
            bucket["paid"] += 1
        elif p["status"] == "pending":
            bucket["pending"] += 1
        else:
            bucket["failed"] += 1
        if key != selected:
            continue
        if paid:
            payers.add(p["user_id"])
            plan = by_plan.setdefault(PLAN_TITLES.get(p["plan"], p["plan"]),
                                      {"count": 0, "sum": 0.0})
            plan["count"] += 1
            plan["sum"] += p["amount"]
        rows.append({
            "when": p["created_at"],
            "who": p["email"] or f"без входа · {p['user_id'][:8]}",
            "what": PLAN_TITLES.get(p["plan"], p["plan"]),
            "amount": p["amount"],
            "commission": p["commission"] or 0,
            "status": PAYMENT_STATUSES.get(p["status"], p["status"]),
            "state": "paid" if paid else "pending" if p["status"] == "pending" else "failed",
        })

    cur = months[selected]
    forecast = None
    if selected == keys[-1]:
        # Прогноз -- по темпу с начала месяца. Первые дни он скачет, но
        # честнее показать его, чем ничего: цель месячная.
        start = datetime(now.year, now.month, 1, tzinfo=tz)
        days = max((now - start).total_seconds() / 86400, 1.0)
        in_month = calendar.monthrange(now.year, now.month)[1]
        forecast = {"projected": round(cur["revenue"] / days * in_month, 2),
                    "daysPassed": round(days, 1), "daysInMonth": in_month}

    return {
        "month": selected,
        "months": list(months.values()),
        "summary": {
            **cur,
            "net": round(cur["revenue"] - cur["commission"], 2),
            "avgCheck": round(cur["revenue"] / cur["paid"], 2) if cur["paid"] else 0,
            "payers": len(payers),
            "byPlan": by_plan,
        },
        "forecast": forecast,
        "goal": FINANCE_GOAL_RUB,
        "allTimeRevenue": storage.stats()["revenue"],
        "payments": rows,
    }


@app.get("/api/admin/notices")
def api_admin_notices(request: Request):
    """
    Что приходило от платёжного сервиса -- и подошла ли формула подписи.

    Отвергнутое уведомление сохраняется вместе с пришедшей подписью и
    той, которую ждали: сравнить есть с чем, и причина видна сразу.
    """
    require_admin(request)
    return {
        "уведомления": [
            {
                "когда": notice["created_at"],
                "принято": bool(notice["accepted"]),
                "причина": notice["reason"],
                "тело": notice["body"],
            }
            for notice in storage.notices()
        ]
    }


@app.get("/api/admin/users")
def api_admin_users(request: Request, q: str = "", offset: int = 0, limit: int = 50):
    """
    Страница списка пользователей.

    Список постраничный намеренно: когда людей станет тысяча, выгружать
    их всех разом -- это полминуты ожидания ради одного экрана. Поиск и
    счётчик треков считает база, а не Python.
    """
    require_admin(request)
    page_size = max(10, min(200, limit))
    found = storage.users_page(q, max(0, offset), page_size)
    return {
        "stats": storage.stats(),
        "total": found["total"],
        "offset": found["offset"],
        "limit": found["limit"],
        "users": [
            {
                "id": u.id,
                "short": u.id[:8],
                "email": u.email,
                "at": u.created_at,
                "freeUsed": u.free_used,
                "paidUntil": u.paid_until,
                "subscribed": u.subscribed,
                "unlimited": u.unlimited,
                "isAdmin": u.is_admin,
                "note": u.note,
                "tracks": tracks,
                "credits": u.credits,
                "balance": u.balance,
            }
            for u, tracks in found["users"]
        ],
    }


@app.post("/api/admin/user/{user_id}")
def api_admin_set(
    user_id: str,
    request: Request,
    unlimited: bool | None = Form(None),
    is_admin: bool | None = Form(None),
    note: str | None = Form(None),
    grant_days: int = Form(0),
):
    """Пометки и ручное продление подписки."""
    me = require_admin(request)
    target = storage.user(user_id)
    if not target:
        raise HTTPException(404, "Пользователь не найден")
    # Снять с себя права можно только при наличии другого администратора,
    # иначе в админку больше никто не войдёт.
    if target.id == me.id and is_admin is False:
        others = [u for u in storage.all_users() if u.is_admin and u.id != me.id]
        if not others:
            raise HTTPException(400, "Нельзя снять права с последнего администратора")

    storage.set_flags(user_id, unlimited=unlimited, is_admin=is_admin, note=note)
    if grant_days:
        billing.grant_subscription(storage, user_id, days=grant_days)
    return {"ok": True, "user": user_id}


@app.get("/account", response_class=HTMLResponse)
def account_page() -> HTMLResponse:
    return page("account.html")


# ----------------------------------------------------------- учётные записи


@app.post("/api/auth/register")
def api_register(request: Request, email: str = Form(...), password: str = Form(...)):
    """
    Завести учётную запись.

    Привязывается к текущей анонимной записи, а не создаёт новую: человек
    мог уже разобрать треки до регистрации, и терять их нельзя.
    """
    try:
        address = auth.normalise_email(email)
        password_hash = auth.hash_password(password)
    except auth.AuthError as exc:
        raise HTTPException(400, str(exc))

    if storage.user_by_email(address):
        raise HTTPException(409, "Такая почта уже зарегистрирована. Войдите.")

    user = current_user(request)
    if user.registered:
        raise HTTPException(400, "Вы уже вошли. Сначала выйдите.")

    try:
        storage.register(user.id, address, password_hash)
    except ValueError as exc:
        raise HTTPException(409, str(exc))

    response = JSONResponse({"ok": True, "email": address})
    attach_cookie(response, user.id)
    return response


@app.post("/api/auth/login")
def api_login(request: Request, email: str = Form(...), password: str = Form(...)):
    try:
        address = auth.normalise_email(email)
    except auth.AuthError as exc:
        raise HTTPException(400, str(exc))

    # Ограничение по почте, а не по адресу клиента: адрес легко меняется,
    # а перебор ведут именно против конкретной учётной записи.
    if login_limiter.blocked(address):
        raise HTTPException(
            429, "Слишком много попыток. Подождите 15 минут или восстановите пароль."
        )

    account = storage.user_by_email(address)
    ok = auth.verify_password(password, account.password_hash if account else None)
    if not account or not ok:
        login_limiter.note_failure(address)
        left = login_limiter.left(address)
        hint = f" Осталось попыток: {left}." if left <= 3 else ""
        raise HTTPException(401, f"Неверная почта или пароль.{hint}")

    login_limiter.reset(address)

    # Треки, разобранные до входа, переносим в аккаунт
    visitor = current_user(request)
    moved = 0
    if visitor.id != account.id and not visitor.registered:
        moved = storage.move_jobs(visitor.id, account.id)
        storage.delete_user_if_empty(visitor.id)

    response = JSONResponse({"ok": True, "email": address, "moved": moved})
    attach_cookie(response, account.id)
    return response


@app.post("/api/auth/logout")
def api_logout():
    """Выйти: кука сбрасывается, следующий заход будет анонимным."""
    response = JSONResponse({"ok": True})
    response.delete_cookie("uid")
    return response


@app.post("/api/auth/forgot")
def api_forgot(request: Request, email: str = Form(...)):
    """
    Выслать ссылку восстановления.

    Ответ одинаков независимо от того, есть такая почта или нет: иначе
    форма превращается в способ узнать, кто зарегистрирован.
    """
    same_answer = {
        "ok": True,
        "message": "Если такая почта зарегистрирована, письмо со ссылкой отправлено.",
    }
    try:
        address = auth.normalise_email(email)
    except auth.AuthError as exc:
        raise HTTPException(400, str(exc))

    ready, why = mailer.available()
    if not ready:
        raise HTTPException(503, why)

    account = storage.user_by_email(address)
    if not account:
        return same_answer

    token, token_hash = auth.make_reset_token()
    storage.create_reset(account.id, token_hash, auth.token_expiry())
    base = str(request.base_url).rstrip("/")
    try:
        mailer.send_reset(address, f"{base}/account?reset={token}")
    except Exception as exc:
        print(f"[forgot] не удалось отправить письмо: {exc}")
        raise HTTPException(503, "Не удалось отправить письмо. Попробуйте позже.")
    return same_answer


@app.post("/api/auth/reset")
def api_reset(token: str = Form(...), password: str = Form(...)):
    try:
        password_hash = auth.hash_password(password)
    except auth.AuthError as exc:
        raise HTTPException(400, str(exc))

    user_id = storage.consume_reset(auth.hash_token(token))
    if not user_id:
        raise HTTPException(400, "Ссылка устарела или уже использована.")

    storage.set_password(user_id, password_hash)
    storage.purge_resets(user_id)   # остальные коды больше не действуют

    response = JSONResponse({"ok": True})
    attach_cookie(response, user_id)
    return response


# ------------------------------------------------------------- обращения


@app.post("/api/support")
def api_support(
    request: Request,
    topic: str = Form(support.DEFAULT_TOPIC),
    body: str = Form(...),
    email: str = Form(""),
):
    """Отправить обращение. Ответ придёт в личный кабинет и на почту."""
    user = current_user(request)
    try:
        key, text = support.validate(topic, body)
    except ValueError as exc:
        raise HTTPException(400, str(exc))

    address = user.email
    if not address and email.strip():
        try:
            address = auth.normalise_email(email)
        except auth.AuthError as exc:
            raise HTTPException(400, str(exc))
    if not address:
        raise HTTPException(
            400, "Укажите почту для ответа или войдите в аккаунт."
        )

    ticket_id = storage.create_ticket(user.id, address, key, text)
    spec = support.TOPICS[key]
    response = JSONResponse(
        {"ok": True, "id": ticket_id, "days": spec["days"], "note": spec["note"]}
    )
    attach_cookie(response, user.id)
    return response


@app.get("/api/support")
def api_my_tickets(request: Request):
    """Свои обращения и ответы на них."""
    user = current_user(request)
    items = []
    for row in storage.user_tickets(user.id):
        items.append(
            {
                "id": row["id"],
                "at": row["created_at"],
                "topic": support.topic_title(row["topic"]),
                "body": row["body"],
                "status": row["status"],
                "answer": row["answer"],
                "answeredAt": row["answered_at"],
            }
        )
    response = JSONResponse({"tickets": items, "topics": support.TOPICS})
    attach_cookie(response, user.id)
    return response


@app.get("/api/admin/tickets")
def api_admin_tickets(request: Request, status: str = ""):
    require_admin(request)
    items = []
    for row in storage.tickets(status or None):
        level = support.urgency(row["created_at"], row["topic"], row["answered_at"])
        items.append(
            {
                "id": row["id"],
                "at": row["created_at"],
                "email": row["email"],
                "userId": row["user_id"][:8],
                "topic": row["topic"],
                "topicTitle": support.topic_title(row["topic"]),
                "body": row["body"],
                "status": row["status"],
                "answer": row["answer"],
                "answeredAt": row["answered_at"],
                "urgency": level.as_dict(),
            }
        )
    return {"tickets": items}


@app.post("/api/admin/ticket/{ticket_id}")
def api_admin_answer(
    ticket_id: str,
    request: Request,
    answer: str = Form(...),
    status: str = Form("answered"),
    notify: bool = Form(True),
):
    """Ответить на обращение. При возможности письмо уходит на почту."""
    require_admin(request)
    ticket = storage.ticket(ticket_id)
    if not ticket:
        raise HTTPException(404, "Обращение не найдено")
    text = (answer or "").strip()
    if len(text) < 2:
        raise HTTPException(400, "Пустой ответ")

    storage.answer_ticket(ticket_id, text, status)

    sent = False
    problem = ""
    if notify and ticket["email"] and mailer.available()[0]:
        try:
            mailer.send(
                ticket["email"],
                "NASLUX — ответ на ваше обращение",
                f"Здравствуйте!\n\nВы писали нам:\n\n{ticket['body']}\n\n"
                f"Наш ответ:\n\n{text}\n\n"
                "Ответить можно в личном кабинете: /account\n",
            )
            sent = True
        except Exception as exc:
            problem = str(exc)
            print(f"[ticket {ticket_id}] письмо не ушло: {exc}")

    return {"ok": True, "emailed": sent, "problem": problem}


@app.get("/i/{code}")
def invite_page(code: str, request: Request):
    """
    Пройти по ссылке-приглашению.

    Регистрироваться заранее не нужно: доступ выдаётся тому, кто открыл
    ссылку, и остаётся при нём, даже если он заведёт учётную запись
    позже -- разобранные треки при этом не теряются.
    """
    user = current_user(request)
    if user.unlimited:
        response = RedirectResponse("/?приглашение=уже", status_code=303)
    elif storage.spend_invite(code):
        storage.set_flags(user.id, unlimited=True, note=f"по приглашению {code.upper()}")
        response = RedirectResponse("/?приглашение=принято", status_code=303)
    else:
        response = RedirectResponse("/?приглашение=нет", status_code=303)
    attach_cookie(response, user.id)
    return response


@app.get("/api/invites")
def api_invites(request: Request):
    user = require_admin(request)
    base = str(request.base_url).rstrip("/")
    return {
        "invites": [
            {**row, "url": f"{base}/i/{row['code']}"} for row in storage.invites(user.id)
        ]
    }


@app.post("/api/invites")
def api_make_invite(request: Request, uses: int = Form(5), note: str = Form("")):
    user = require_admin(request)
    code = storage.create_invite(user.id, uses=max(1, min(100, uses)), note=note[:120])
    base = str(request.base_url).rstrip("/")
    return {"code": code, "url": f"{base}/i/{code}"}


@app.delete("/api/invites/{code}")
def api_drop_invite(code: str, request: Request):
    user = require_admin(request)
    storage.drop_invite(code, user.id)
    return {"ok": True}


@app.get("/pricing", response_class=HTMLResponse)
def pricing_page() -> HTMLResponse:
    """
    Тарифы. Цены подставляются на сервере, а не запрашиваются со
    страницы: иначе человек секунду видит пустоту на месте главного,
    за чем он сюда и пришёл.
    """
    return page("pricing.html", {
        "price": f"{billing.PRICE_RUB:.0f}",
        "priceSingle": f"{billing.PRICE_SINGLE_RUB:.0f}",
        "topupMin": f"{billing.TOPUP_MIN_RUB:.0f}",
        "topupMax": f"{billing.TOPUP_MAX_RUB:.0f}",
        "studio10": f"{billing.PLANS['studio10']['price']:.0f}",
        "studio30": f"{billing.PLANS['studio30']['price']:.0f}",
        "studioSingle": f"{studio.SERVICES['restyle'].price:.0f}",
    })


@app.get("/privacy", response_class=HTMLResponse)
def privacy_page() -> HTMLResponse:
    return page("privacy.html")


@app.get("/offer", response_class=HTMLResponse)
def offer_page() -> HTMLResponse:
    return page("offer.html")


@app.get("/library", response_class=HTMLResponse)
def library_page() -> HTMLResponse:
    return page("library.html")


@app.get("/player/{job_id}", response_class=HTMLResponse)
def player_page(job_id: str) -> HTMLResponse:
    if not storage.job(job_id):
        raise HTTPException(404, "Задание не найдено")
    return page("player.html")


# --------------------------------------------------------------------- API

@app.get("/api/me")
def api_me(request: Request):
    user = current_user(request)
    access = billing.check_access(user)
    payload = access.as_dict()
    payload["paymentReady"] = billing.provider().configured()
    payload["separationReady"] = separate.available()[0]
    payload["recognitionReady"] = audioin.available()[0]
    payload["tunings"] = list(TUNINGS)
    payload["grids"] = list(GRIDS)
    payload["models"] = list(separate.MODELS)
    payload["qualities"] = list(separate.QUALITY)
    payload["email"] = user.email
    payload["registered"] = user.registered
    payload["mailReady"] = mailer.available()[0]
    payload["isAdmin"] = user.is_admin
    payload["unlimited"] = user.unlimited
    payload["studioCredits"] = user.studio_credits
    payload["lyricsReady"] = lyrics_mod.available()[0]
    payload["lyricsModels"] = list(lyrics_mod.MODELS)
    payload["lyricsLanguages"] = list(lyrics_mod.LANGUAGES)
    payload["vocabulary"] = audiochords.DEFAULT_VOCABULARY
    payload["jobs"] = [
        {"id": j.id, "name": j.filename, "status": j.status, "at": j.created_at,
         "stage": j.stage, "progress": j.progress}
        for j in storage.root_jobs(user.id, 10)
    ]
    response = JSONResponse(payload)
    attach_cookie(response, user.id)
    return response


@app.post("/api/upload")
async def api_upload(
    request: Request,
    file: UploadFile = File(...),
    tuning: str = Form(""),
    capo: int = Form(0),
    tempo: int = Form(0),
    min_chord: float = Form(0.9),
    vocabulary: int = Form(audiochords.DEFAULT_VOCABULARY),
    chords: str = Form(""),
    grid: str = Form(""),
    separate_track: bool = Form(False),
    model: str = Form(""),
    quality: str = Form(""),
    remove_ghosts: bool = Form(True),
    max_polyphony: int = Form(0),
):
    user = current_user(request)
    access = billing.check_access(user)
    if not access.allowed:
        raise HTTPException(402, access.reason)

    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in ALLOWED:
        raise HTTPException(
            400, f"Формат {suffix or 'неизвестный'} не поддерживается. Нужен {', '.join(ALLOWED)}"
        )

    options = {
        "tuning": tuning or None,
        "capo": capo,
        "tempo": tempo,
        "minChord": min_chord,
        "vocabulary": max(0, min(24, vocabulary)),
        "allowed": chords.strip() or None,
        "grid": grid or None,
        "separate": separate_track,
        "model": model or separate.DEFAULT_MODEL,
        "quality": quality if quality in separate.QUALITY else separate.DEFAULT_QUALITY,
        "removeGhosts": remove_ghosts,
        "maxPolyphony": max_polyphony,
    }
    options = {k: v for k, v in options.items() if v is not None}

    job = storage.create_job(user.id, file.filename or "upload", options)
    upload_dir = os.path.join(DATA_DIR, "uploads", job.id)
    os.makedirs(upload_dir, exist_ok=True)
    target = os.path.join(upload_dir, f"{safe_stem(file.filename)}{suffix}")

    size = 0
    limit = MAX_UPLOAD_MB * 1024 * 1024
    with open(target, "wb") as out:
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            if size > limit:
                out.close()
                shutil.rmtree(upload_dir, ignore_errors=True)
                storage.update_job(job.id, status="error", error="Файл слишком большой")
                raise HTTPException(413, f"Файл больше {MAX_UPLOAD_MB} МБ")
            out.write(chunk)

    # Пробная песня списывается в момент постановки в очередь, а не по
    # завершении: иначе один и тот же файл можно было бы гонять бесконечно,
    # обрывая задание на полпути.
    if not billing.consume(storage, user):
        # Доступ на входе в функцию проверялся по снимку, снятому до
        # загрузки файла -- у него было время устареть (например, тот же
        # пользователь параллельно запустил ещё один разбор и списал
        # последнюю пробную песню/кредит первым). Раз списывать оказалось
        # не с чего, разбор не запускаем -- иначе он ушёл бы бесплатно.
        # access.reason тут не годится: это сообщение из проверки ДО
        # загрузки ("осталось песен — 1"), не про то, что списать не
        # вышло -- показывать его как причину отказа только запутает.
        race_reason = (
            "Лимит уже израсходован — похоже, вы запустили разбор ещё "
            "одной песни параллельно. Обновите страницу и попробуйте снова."
        )
        shutil.rmtree(upload_dir, ignore_errors=True)
        storage.update_job(job.id, status="error", error=race_reason)
        raise HTTPException(402, race_reason)
    storage.update_job(job.id, counted=True)
    runner.submit_analysis(job.id, target)

    response = JSONResponse({"jobId": job.id})
    attach_cookie(response, user.id)
    return response


@app.get("/api/job/{job_id}")
def api_job(job_id: str, request: Request):
    user = current_user(request)
    job = storage.job(job_id)
    if not job or job.user_id != user.id:
        raise HTTPException(404, "Задание не найдено")
    payload = {
        "id": job.id,
        "status": job.status,
        "stage": job.stage,
        "progress": job.progress,
        "error": job.error,
        "name": job.filename,
    }
    if job.status == "done" and job.result:
        payload["result"] = {
            key: value for key, value in job.result.items() if key != "paths"
        }
        # Аппликатуры появились позже, чем часть разборов. Считать их
        # заново -- доли секунды, а без этого человек, открывший старый
        # трек, картинок просто не увидит и решит, что их нет вовсе.
        if not payload["result"].get("shapes") and payload["result"].get("chords"):
            payload["result"]["shapes"] = jobs_module._shapes_for(
                payload["result"]["chords"], job.settings or {}
            )
    # Табы, сделанные раньше, должны находиться и после перезагрузки
    # страницы: человек вернулся к треку за файлами, а не делать всё заново.
    labels = {p["key"]: p["label"] for p in ((job.result or {}).get("parts") or [])}
    made = []
    for child in storage.child_jobs(job.id):
        stem = (child.settings or {}).get("stem", "")
        made.append(
            {
                "id": child.id,
                "stem": stem,
                "label": labels.get(stem, stem),
                "status": child.status,
                "stage": child.stage,
                "progress": child.progress,
                "files": [k for k, v in ((child.result or {}).get("files") or {}).items() if v],
            }
        )
    if made:
        payload["made"] = made
    return payload


@app.get("/api/library")
def api_library(request: Request):
    """
    Треки пользователя вместе с тем, что для них уже сделано.

    Смысл кабинета в том, чтобы вернуться к треку и найти всё на месте,
    а не разбирать его заново.
    """
    user = current_user(request)
    items = []
    for job in storage.root_jobs(user.id):
        children = storage.child_jobs(job.id)
        result = job.result or {}
        items.append(
            {
                "id": job.id,
                "name": job.filename,
                "at": job.created_at,
                "status": job.status,
                "stage": job.stage,
                "progress": job.progress,
                "tempo": result.get("tempo"),
                "chords": len(result.get("chords") or []),
                "parts": [p["label"] for p in (result.get("parts") or [])],
                "hasLyrics": bool(result.get("lyrics")),
                "made": [
                    {
                        "id": child.id,
                        "stem": (child.settings or {}).get("stem", ""),
                        "status": child.status,
                        "stage": child.stage,
                        "progress": child.progress,
                        "files": list(((child.result or {}).get("files") or {}).keys()),
                    }
                    for child in children
                ],
            }
        )
    response = JSONResponse({"tracks": items})
    attach_cookie(response, user.id)
    return response


@app.post("/api/job/{job_id}/lyrics")
def api_make_lyrics(
    job_id: str,
    request: Request,
    model: str = Form(lyrics_mod.DEFAULT_MODEL),
    language: str = Form(lyrics_mod.DEFAULT_LANGUAGE),
):
    """Распознать текст песни по вокальной партии."""
    user = current_user(request)
    job = storage.job(job_id)
    if not job or job.user_id != user.id or job.status != "done" or not job.result:
        raise HTTPException(404, "Разбор ещё не готов")
    ok, why = lyrics_mod.available()
    if not ok:
        raise HTTPException(503, why)
    # Язык кладём в настройки задания: на пении автоопределение ошибается
    # заметно чаще, чем на речи, и, приняв русский за болгарский, Whisper
    # выдаёт правдоподобную бессмыслицу вместо текста.
    job = storage.job(job_id)
    if job:
        storage.update_job(job_id, settings={**(job.settings or {}), "language": language})
    runner.submit_lyrics(job_id, model)
    return {"ok": True}


@app.post("/api/job/{job_id}/separate")
def api_separate_later(job_id: str, request: Request):
    """
    Разделить на партии уже разобранный трек.

    Сначала берут аккорды -- это быстро, -- а партии нужны потом, когда
    дошло до конкретной гитары. Грузить тот же файл заново, теряя
    сделанные табы, человек не должен: исходник лежит на диске.
    """
    user = current_user(request)
    job = storage.job(job_id)
    if not job or job.user_id != user.id:
        raise HTTPException(404, "Трек не найден")
    if job.status == "running":
        raise HTTPException(409, "Этот трек сейчас обрабатывается")
    if not job.result:
        raise HTTPException(409, "Разбор ещё не готов")
    if (job.result.get("paths") or {}).get("parts", {}).keys() - {"full"}:
        raise HTTPException(409, "Трек уже разделён на партии")

    ok, why = separate.available()
    if not ok:
        raise HTTPException(503, why)
    runner.submit_separation(job_id)
    return {"ok": True}


@app.post("/api/job/{job_id}/tabs/{stem_key}")
def api_make_tabs(job_id: str, stem_key: str, request: Request):
    """Создать MIDI и табы для выбранной партии."""
    user = current_user(request)
    parent = storage.job(job_id)
    if not parent or parent.user_id != user.id or parent.status != "done" or not parent.result:
        raise HTTPException(404, "Разбор ещё не готов")
    parts = (parent.result.get("paths") or {}).get("parts", {})
    if stem_key not in parts:
        raise HTTPException(404, "Такой партии нет")

    # Табы из полного микса -- это мусор, и предлагать их нечестно.
    # Basic Pitch слышит ВСЁ: вокал, барабаны, бас и гитару разом, и всё
    # это раскладывается на один гриф. На реальной песне получилось 1118
    # нот по всему грифу до семнадцатого лада -- сыграть это нельзя.
    if stem_key == "full" and not parent.result.get("isMidi"):
        ok, _why = separate.available()
        if ok:
            raise HTTPException(
                409,
                "Сначала разделите трек на партии: табы из полного микса "
                "бесполезны — в них попадут и вокал, и барабаны. Кнопка "
                "«Разделить на партии» под списком.",
            )

    child = storage.create_job(
        user.id, f"{parent.filename} — {stem_key}", {"parent": job_id, "stem": stem_key}
    )
    runner.submit_tabs(child.id, job_id, stem_key)
    return {"jobId": child.id}


@app.delete("/api/job/{job_id}")
def api_delete_job(job_id: str, request: Request):
    """
    Удалить трек вместе с файлами.

    Файлы удаляются с диска, а не только строки из базы: иначе место
    занято, а человек уверен, что убрал за собой.
    """
    user = current_user(request)
    job = storage.job(job_id)
    if not job:
        raise HTTPException(404, "Трек не найден")
    if job.user_id != user.id and not user.is_admin:
        raise HTTPException(403, "Это не ваш трек")

    removed = storage.delete_job(job_id)
    freed = 0
    for identifier in removed:
        for folder in (
            os.path.join(DATA_DIR, "uploads", identifier),
            os.path.join(DATA_DIR, "results", identifier),
        ):
            if os.path.isdir(folder):
                for root, _dirs, files in os.walk(folder):
                    for name in files:
                        try:
                            freed += os.path.getsize(os.path.join(root, name))
                        except OSError:
                            pass
                shutil.rmtree(folder, ignore_errors=True)
    return {"ok": True, "deleted": len(removed), "freedBytes": freed}


@app.get("/api/file/{job_id}/part/{stem_key}")
def api_part_file(job_id: str, stem_key: str, request: Request):
    """Аудио одной партии -- его слушают в плеере."""
    user = current_user(request)
    job = storage.job(job_id)
    if not job or job.user_id != user.id or not job.result:
        raise HTTPException(404, "Файл не готов")
    path = (job.result.get("paths") or {}).get("parts", {}).get(stem_key)
    if not path or not os.path.isfile(path):
        raise HTTPException(404, "Партия не найдена")
    labels = {p["key"]: p["label"] for p in (job.result.get("parts") or [])}
    return FileResponse(path, filename=download_name(job.filename, labels.get(stem_key, stem_key),
                                                     Path(path).suffix))


@app.get("/api/file/{job_id}/{kind}")
def api_file(job_id: str, kind: str, request: Request):
    user = current_user(request)
    job = storage.job(job_id)
    if not job or job.user_id != user.id or not job.result:
        raise HTTPException(404, "Файл не готов")
    path = (job.result.get("paths") or {}).get(kind)
    if not path or not os.path.isfile(path):
        raise HTTPException(404, "Файл не найден")
    parent = storage.job((job.settings or {}).get("parent", "")) if job.settings else None
    title = (parent or job).filename
    stem = (job.settings or {}).get("stem", "")
    return FileResponse(path, filename=download_name(title, stem, Path(path).suffix))


# ------------------------------------------------------------------ студия

@app.get("/studio", response_class=HTMLResponse)
def studio_page() -> HTMLResponse:
    return page("studio.html")


def _studio_job_payload(job) -> dict:
    settings = job.settings or {}
    result = job.result or {}
    folder = studio_runner.folder(job.id)
    files = [{**f, "url": f"/api/studio/file/{job.id}/{f['name']}"}
             for f in result.get("files") or []
             if os.path.isfile(os.path.join(folder, f["name"]))]
    return {
        "id": job.id, "name": job.filename, "at": job.created_at, "status": job.status,
        "stage": job.stage, "progress": job.progress, "error": job.error,
        "mode": settings.get("mode"),
        "title": settings.get("title", "") + (
            f" · {settings.get('variant', 'новой версии').lower()}" if settings.get("from") else ""),
        "charged": settings.get("charged", 0),
        "bpm": (result.get("settings") or {}).get("bpm"),
        "key": (result.get("settings") or {}).get("key_scale"),
        "files": files,
        # Файлы Студии уборка удаляет через 14 дней (deploy/cleanup.py)
        "expired": job.status == "done" and not files,
    }


@app.get("/api/studio")
def api_studio(request: Request):
    user = current_user(request)
    ready, why = studio.available()
    response = JSONResponse({
        "ready": ready, "why": why,
        "services": {k: {"title": v.title, "price": v.price} for k, v in studio.SERVICES.items()},
        "presets": {k: v[0] for k, v in studio.PRESETS.items()},
        "tracks": studio.TRACKS,
        "balance": user.balance, "unlimited": user.unlimited, "registered": user.registered,
        "studioCredits": user.studio_credits,
        "restyleOpen": studio.RESTYLE_OPEN or user.unlimited,
        "email": user.email, "maxMb": MAX_UPLOAD_MB, "maxSeconds": studio.MAX_SECONDS,
        "jobs": [_studio_job_payload(j) for j in storage.studio_jobs(user.id)],
    })
    attach_cookie(response, user.id)
    return response


@app.post("/api/studio")
async def api_studio_start(
    request: Request,
    file: UploadFile = File(...),
    mode: str = Form("restyle"),
    preset: str = Form("numetal"),
    prompt: str = Form(""),
    lyrics: str = Form(""),
    audio_influence: float = Form(0.5),
    style_influence: float = Form(0.5),
    weirdness: float = Form(0.3),
    track: str = Form("drums"),
    language: str = Form("ru"),
):
    user = current_user(request)
    ready, why = studio.available()
    if not ready:
        raise HTTPException(503, why)
    service = studio.SERVICES.get(mode)
    if service is None:
        raise HTTPException(400, "Неизвестная услуга")
    if mode == "restyle" and not (studio.RESTYLE_OPEN or user.unlimited):
        raise HTTPException(409, "Переделка в другой стиль переезжает на новый движок и скоро "
                                 "вернётся. Разделение на партии и дописывание партии работают.")
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in ALLOWED or suffix in (".mid", ".midi"):
        raise HTTPException(400, "Нужен аудиофайл: mp3, wav, flac, ogg, m4a")
    if mode == "enrich" and track not in studio.TRACKS:
        raise HTTPException(400, "Выберите партию, которую дописать")
    style = prompt.strip()[:500] or studio.PRESETS.get(preset, studio.PRESETS["numetal"])[1]
    by_pack = mode in studio.PACK_MODES and user.studio_credits > 0
    if not user.unlimited and not by_pack and user.balance < service.price:
        raise HTTPException(402, f"{service.title} стоит {service.price:.0f} ₽, на балансе "
                                 f"{user.balance:.0f} ₽. Пополните баланс или возьмите пакет "
                                 "генераций на странице тарифов.")

    clamp = lambda v: max(0.0, min(1.0, float(v)))  # noqa: E731
    knobs = {"audio_influence": clamp(audio_influence),
             "style_influence": clamp(style_influence), "weirdness": clamp(weirdness)}
    settings = {"kind": "studio", "mode": mode, "title": service.title, "preset": preset,
                **knobs, "track": track, "charged": 0}
    job = storage.create_job(user.id, file.filename or "track", settings)
    folder = studio_runner.folder(job.id)
    os.makedirs(folder, exist_ok=True)
    source = os.path.join(folder, f"source{suffix}")
    size, limit = 0, MAX_UPLOAD_MB * 1024 * 1024
    with open(source, "wb") as out:
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            if size > limit:
                out.close()
                shutil.rmtree(folder, ignore_errors=True)
                storage.update_job(job.id, status="error", error="Файл слишком большой")
                raise HTTPException(413, f"Файл больше {MAX_UPLOAD_MB} МБ")
            out.write(chunk)

    _studio_charge_and_submit(request, user, job.id, service, folder, {
        "mode": mode, "prompt": style, "lyrics": lyrics.strip()[:5000], **knobs,
        # Две версии за раз: авторы ACE-Step советуют выбирать из
        # нескольких, а GPU на вторую тратит секунды.
        "variants": 2,
        "track": track, "language": language if language in ("ru", "en") else "ru",
    })
    response = JSONResponse({"jobId": job.id})
    attach_cookie(response, user.id)
    return response


def _studio_charge_and_submit(request: Request, user, job_id: str, service, folder: str,
                              worker_input: dict) -> None:
    """Списать деньги и отдать задачу воркеру: общая часть всех запусков Студии."""
    job = storage.job(job_id)
    settings = dict(job.settings or {})
    # Списываем до запуска и одним атомарным запросом: два параллельных
    # запуска не должны потратить одни и те же деньги дважды.
    if not user.unlimited and worker_input["mode"] in studio.PACK_MODES \
            and storage.spend_studio_credit(user.id):
        # Генерация из пакета: деньги не трогаем, при сбое вернём генерацию.
        settings["charged_credit"] = True
        storage.update_job(job_id, settings=settings, counted=True)
    elif not user.unlimited:
        if not storage.spend_balance(user.id, service.price):
            shutil.rmtree(folder, ignore_errors=True)
            storage.update_job(job_id, status="error", error="Не хватило денег на балансе")
            raise HTTPException(402, "Не хватило денег на балансе — пополните и попробуйте снова")
        settings["charged"] = service.price
        storage.update_job(job_id, settings=settings, counted=True)

    base = str(request.base_url).rstrip("/")
    studio_runner.submit(job_id, {
        **worker_input, "seconds": studio.MAX_SECONDS,
        "audio_url": studio.link(base, SECRET, job_id, "source"),
        "upload_url": studio.link(base, SECRET, job_id, "upload"),
    })


@app.post("/api/studio/{job_id}/stems")
def api_studio_split_result(job_id: str, request: Request, file: str = Form("")):
    """Разделить на партии уже готовую переделку -- без повторной загрузки."""
    user = current_user(request)
    parent = storage.job(job_id)
    if (not parent or parent.user_id != user.id
            or (parent.settings or {}).get("kind") != "studio" or parent.status != "done"):
        raise HTTPException(404, "Готовая работа не найдена")
    files = [f for f in (parent.result or {}).get("files") or []
             if os.path.isfile(os.path.join(studio_runner.folder(job_id), f["name"]))
             and (not file or f["name"] == file)]
    if not files:
        raise HTTPException(409, "Файлы этой работы уже удалены по сроку хранения")
    ready, why = studio.available()
    if not ready:
        raise HTTPException(503, why)
    service = studio.SERVICES["stems"]
    if not user.unlimited and user.balance < service.price:
        raise HTTPException(402, f"{service.title} стоит {service.price:.0f} ₽, на балансе "
                                 f"{user.balance:.0f} ₽. Пополните баланс на странице тарифов.")
    title = f"{(parent.settings or {}).get('title', '')}: {parent.filename}"
    variant = studio.label_of((parent.settings or {}).get("mode", ""), files[0]["name"])
    job = storage.create_job(user.id, parent.filename, {
        "kind": "studio", "mode": "stems", "title": service.title, "from": job_id,
        "variant": variant, "charged": 0})
    folder = studio_runner.folder(job.id)
    os.makedirs(folder, exist_ok=True)
    shutil.copyfile(os.path.join(studio_runner.folder(job_id), files[0]["name"]),
                    os.path.join(folder, "source.mp3"))
    _studio_charge_and_submit(request, user, job.id, service, folder, {"mode": "stems"})
    return {"jobId": job.id, "from": title}


def _studio_link_job(job_id: str, purpose: str, e: int, s: str):
    if not studio.check_link(SECRET, job_id, purpose, e, s):
        raise HTTPException(403, "Ссылка недействительна")
    job = storage.job(job_id)
    if not job or (job.settings or {}).get("kind") != "studio":
        raise HTTPException(404, "Задача не найдена")
    return job


@app.get("/api/studio/source/{job_id}")
def api_studio_source(job_id: str, e: int = 0, s: str = ""):
    """Исходник для воркера -- по подписанной ссылке, без куки."""
    _studio_link_job(job_id, "source", e, s)
    found = [f for f in os.listdir(studio_runner.folder(job_id)) if f.startswith("source.")]
    if not found:
        raise HTTPException(404, "Исходник не найден")
    return FileResponse(os.path.join(studio_runner.folder(job_id), found[0]))


@app.post("/api/studio/upload/{job_id}")
async def api_studio_upload(job_id: str, request: Request, e: int = 0, s: str = ""):
    """Результат от воркера -- по подписанной ссылке, по файлу за запрос."""
    _studio_link_job(job_id, "upload", e, s)
    name = studio.safe_file_name(request.headers.get("x-file-name", ""))
    if not name:
        raise HTTPException(400, "Недопустимое имя файла")
    target = os.path.join(studio_runner.folder(job_id), name)
    size, limit = 0, 200 * 1024 * 1024
    with open(target, "wb") as out:
        async for chunk in request.stream():
            size += len(chunk)
            if size > limit:
                out.close()
                os.remove(target)
                raise HTTPException(413, "Слишком большой файл")
            out.write(chunk)
    return {"ok": True, "bytes": size}


@app.get("/api/studio/file/{job_id}/{name}")
def api_studio_file(job_id: str, name: str, request: Request):
    user = current_user(request)
    job = storage.job(job_id)
    safe = studio.safe_file_name(name)
    if not job or job.user_id != user.id or not safe:
        raise HTTPException(404, "Файл не найден")
    path = os.path.join(studio_runner.folder(job_id), safe)
    if not os.path.isfile(path):
        raise HTTPException(404, "Файл не найден")
    label = studio.label_of((job.settings or {}).get("mode", ""), safe)
    return FileResponse(path, filename=download_name(job.filename, label, ".mp3"))


@app.delete("/api/studio/{job_id}")
def api_studio_delete(job_id: str, request: Request):
    user = current_user(request)
    job = storage.job(job_id)
    if not job or job.user_id != user.id or (job.settings or {}).get("kind") != "studio":
        raise HTTPException(404, "Задача не найдена")
    if job.status in ("queued", "running"):
        raise HTTPException(409, "Задача ещё выполняется")
    shutil.rmtree(studio_runner.folder(job_id), ignore_errors=True)
    storage.delete_job(job_id)
    return {"ok": True}


# ------------------------------------------------------------------ оплата

def _start_payment(request: Request, user, amount: float, title: str, plan: str) -> dict:
    """
    Общая часть создания платежа: подписка, разовый трек и пополнение
    баланса отличаются только суммой, названием и тем, что зачислится
    по итогу (plan) -- сам разговор с платёжным шлюзом у них один.
    """
    if not user.registered:
        # Анонимный доступ держится на куке uid: потеряй её (приватная
        # вкладка, смена телефона, очистка cookies) -- и оплаченное
        # исчезнет без возможности восстановить, потому что нет ни
        # почты, ни пароля, которыми можно опознать владельца. На
        # странице тарифов эта проверка уже стоит на клиенте (pricing.js),
        # но /api/subscribe и /api/topup дергались и из paywall'а на
        # странице загрузки (upload.js) без неё -- сервер обязан
        # требовать регистрацию сам, а не полагаться на то, что каждый
        # вызывающий код её не забудет.
        raise HTTPException(
            403,
            'Сначала <a href="/account">заведите учётную запись</a> — иначе '
            "оплаченное потеряется при смене браузера.",
        )
    gateway = billing.provider()
    if not gateway.configured():
        raise HTTPException(
            503,
            "Приём оплаты пока не подключён. Нужны реквизиты магазина "
            "в переменных окружения, см. web/README.md.",
        )
    base = str(request.base_url).rstrip("/")
    try:
        created = gateway.create_payment(
            user.id, amount, f"{base}/?paid=1",
            title=title,
            notify_url=f"{base}/api/webhook/{gateway.name}",
            email=user.email or "",
            fail_url=f"{base}/?paid=0",
        )
    except billing.PaymentError as error:
        # Неудачную попытку сохраняем наравне с уведомлениями: по ней
        # видно, что именно ответил платёжный сервис. Иначе владелец
        # знает лишь то, что "не получилось", -- и чинить нечего.
        storage.save_notice(
            gateway.name, {"попытка оплаты": error.details}, False, str(error)[:300]
        )
        raise HTTPException(502, str(error)) from error
    except Exception as error:                       # noqa: BLE001
        storage.save_notice(
            gateway.name, {"попытка оплаты": {"ошибка": repr(error)}}, False,
            "неожиданная ошибка",
        )
        raise HTTPException(
            502, f"Платёжный сервис не ответил как ожидалось: {error}"
        ) from error

    storage.create_payment(user.id, amount, created.get("id"), plan=plan)
    url = (created.get("confirmation") or {}).get("confirmation_url")
    return {"paymentUrl": url, "paymentId": created.get("id"), "plan": plan}


PLAN_TITLES = {"single": "один трек", "month": "подписка на месяц", "topup": "пополнение баланса",
               "studio10": "Студия: 10 генераций", "studio30": "Студия: 30 генераций"}
PAYMENT_STATUSES = {"succeeded": "оплачен", "pending": "ожидает оплаты",
                    "failed": "не прошёл", "canceled": "отменён"}


@app.get("/api/payments")
def api_payments(request: Request):
    """
    История своих платежей.

    Без неё человек, заплативший и не увидевший результата, не может
    понять главного: дошли деньги или нет. Статус "ожидает" значит, что
    подтверждения от платёжного сервиса ещё не было; "оплачен" -- что
    зачислено.
    """
    user = current_user(request)
    return {
        "payments": [
            {
                "when": p["created_at"],
                "amount": p["amount"],
                "what": PLAN_TITLES.get(p["plan"], p["plan"]),
                "status": PAYMENT_STATUSES.get(p["status"], p["status"]),
                "paid": p["status"] == "succeeded",
            }
            for p in storage.user_payments(user.id)
        ]
    }


@app.post("/api/subscribe")
def api_subscribe(request: Request, plan: str = Form("month")):
    """Создать платёж: подписка на месяц или один трек."""
    if plan not in billing.PLANS:
        raise HTTPException(400, "Неизвестный тариф")
    user = current_user(request)
    spec = billing.PLANS[plan]
    return _start_payment(request, user, spec["price"], spec["title"], plan)


@app.post("/api/topup")
def api_topup(request: Request, amount: float = Form(...)):
    """
    Пополнить баланс на любую сумму в разрешённых границах.

    Деньги ложатся на счёт сразу по оплате, но не тратятся: спишутся
    ровно по цене трека, когда человек реально запустит разбор песни
    (billing.consume), а не в момент пополнения.
    """
    if not (billing.TOPUP_MIN_RUB <= amount <= billing.TOPUP_MAX_RUB):
        raise HTTPException(
            400,
            f"Сумма пополнения — от {billing.TOPUP_MIN_RUB:.0f} "
            f"до {billing.TOPUP_MAX_RUB:.0f} ₽",
        )
    user = current_user(request)
    return _start_payment(
        request, user, amount, f"Пополнение баланса на {amount:.0f} ₽", "topup"
    )


@app.post("/api/webhook/{gateway_name}")
async def api_webhook(gateway_name: str, request: Request):
    """
    Уведомление об оплате.

    Адрес включает имя сервиса, потому что его прописывают в кабинете
    мерчанта и менять там что-то задним числом неудобно: пусть у каждого
    будет свой, а смена провайдера не требует править настройки у старого.
    Форма тела бывает и JSON, и обычной формой -- принимаем обе.
    """
    # Сырое тело обязательно: подпись считается именно от него, байт в
    # байт. Если разобрать JSON и собрать заново, порядок ключей или
    # пробелы изменятся -- и подпись не сойдётся, хотя уведомление
    # настоящее. В документации GetPlatinum это оговорено прямо.
    raw = await request.body()
    try:
        import json as _json

        payload = _json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict):
            payload = {"значение": payload}
    except Exception:
        payload = dict(await request.form())

    gateway = billing.provider()
    verified = gateway.verify(raw, request.headers, payload)
    if not verified:
        # Отвергнутое уведомление сохраняем обязательно: именно по нему
        # потом подбирается формула подписи. Без записи причина отказа
        # теряется навсегда, и остаётся гадать, почему оплата не доходит.
        # Сохраняем и то, что помогает понять причину: пришедшую подпись
        # и ту, что ждали. Сам ключ, разумеется, никуда не попадает.
        expected = ""
        if hasattr(gateway, "checksum") and getattr(gateway, "secret", ""):
            expected = gateway.checksum(raw, gateway.secret)
        storage.save_notice(
            gateway_name,
            {
                "тело": payload,
                "подпись пришла": request.headers.get("X-Checksum", "(заголовка нет)"),
                "подпись ожидалась": expected or "(не посчитать)",
                "длина тела": len(raw),
            },
            False,
            "подпись не сошлась",
        )
        raise HTTPException(401, "Подпись уведомления не сошлась")
    provider_id, status = verified
    if status not in ("succeeded", "failed"):
        # Уведомление не про исход оплаты -- например, "заказ создан".
        # Статус платежа оно не меняет, и ищем платёж не раньше исхода:
        # "заказ создан" приходит, пока мы ещё не успели записать платёж
        # к себе, и раньше отвергалось как "платёж не найден".
        storage.save_notice(gateway_name, payload, True,
                            "заказ создан, ждём оплаты" if status == "created"
                            else f"уведомление не об оплате ({status})")
        return {"ok": True}
    record = storage.payment_by_provider(provider_id)
    if not record:
        storage.save_notice(gateway_name, payload, False, "платёж не найден")
        raise HTTPException(404, "Платёж не найден")
    if status != "succeeded":
        storage.save_notice(gateway_name, payload, True, status)
        # Уже зачисленный платёж "неудачей" не перетираем: иначе повтор
        # успешного уведомления после неё начислил бы второй раз
        # (mark_paid_once смотрит именно на статус).
        if record.get("status") != "succeeded":
            storage.set_payment_status(record["id"], status)
        return {"ok": True}

    # Начисляем ровно один раз. Платёжные сервисы повторяют уведомления,
    # если ответ потерялся, -- и это нормально; а вот выдать за один
    # платёж два трека или два месяца подписки -- уже нет.
    first_time = storage.mark_paid_once(record["id"])
    if first_time:
        billing.apply_plan(
            storage, record["user_id"], record.get("plan") or "month", record.get("amount")
        )
    # Комиссия -- для финансового отчёта: GetPlatinum присылает её в
    # копейках в paymentData успешного уведомления.
    commission = (payload.get("paymentData") or {}).get("commission")
    if isinstance(commission, (int, float)):
        storage.set_commission(record["id"], commission / 100)
    storage.save_notice(
        gateway_name, payload, True,
        "succeeded" if first_time else "succeeded (повтор, начислять нечего)",
    )
    return {"ok": True}


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    """Браузер запрашивает иконку сам; без неё в консоли висит 404."""
    return FileResponse(STATIC_DIR / "favicon.svg", media_type="image/svg+xml")


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
