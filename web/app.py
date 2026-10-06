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

import asyncio
import os
import re
import secrets
import subprocess
import urllib.parse
import shutil
import sys
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from itsdangerous import BadSignature, URLSafeSerializer

from midi2tab import audiochords, audioin, lyrics as lyrics_mod, separate
from midi2tab.timing import GRIDS
from midi2tab.tuning import TUNINGS

from . import auth, billing, mailer, oauth, seo, studio, support
from . import jobs as jobs_module
from .jobs import JobRunner
from .storage import Storage

DATA_DIR = os.environ.get("MIDI2TAB_DATA", "data")
SECRET = os.environ.get("MIDI2TAB_SECRET", "")
MAX_UPLOAD_MB = int(os.environ.get("MIDI2TAB_MAX_MB", "60"))
FREE_CHORDS_PER_DAY = 30  # бесплатные разборы аккордов в сутки на человека (защита сервера)
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
    html = page_stamp(html)
    return HTMLResponse(
        html,
        # Саму страницу кешировать нельзя: в ней и лежит отпечаток, по
        # которому браузер узнаёт, что статика обновилась.
        headers={"Cache-Control": "no-cache, must-revalidate"},
    )


def page_stamp(html: str) -> str:
    """Отпечаток версии в ссылках на статику -- чтобы браузер брал свежую."""
    return re.sub(r'(/static/[\w./-]+\.(?:js|css|svg))"', rf'\1?v={STAMP}"', html)


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
    # Разборы, разделения, текст и табы из потока, который автодеплой убил
    # прошлым перезапуском, иначе остаются "running" и деньги за них --
    # списанными навсегда (см. JobRunner.recover_interrupted).
    recovered = runner.recover_interrupted()
    if recovered:
        print(f"  Разбор: закрыли прерванные перезапуском задания — {recovered}")
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

HOME_TITLE = "NASLUX — аккорды, табы и MIDI по песне и создание музыки нейросетью"
HOME_DESCRIPTION = ("Разберите любую песню на аккорды (бесплатно), дорожки, табы и MIDI — или "
                    "создайте свою: нейросеть напишет песню по тексту и сделает кавер.")
STUDIO_TITLE = "Студия NASLUX — создать песню нейросетью, кавер, дорожки и MIDI"
STUDIO_DESCRIPTION = ("Создайте песню нейросетью по тексту, сделайте кавер своей песни, разделите "
                      "трек на дорожки с MIDI, смените темп и тональность. На русском, оплата картой РФ.")


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    # Главная -- разбор трека (загрузка -- главное действие) и вход в Студию
    return page("index.html", {"HEAD": seo.head_tags("/", HOME_TITLE, HOME_DESCRIPTION)
                               + "\n" + seo.app_ld(), "FEATURES": seo.features_nav(),
                               "TITLE": HOME_TITLE, "DESCRIPTION": HOME_DESCRIPTION})


@app.get("/chords")
def chords_page() -> RedirectResponse:
    # Недолго здесь жил разбор, пока главной была Студия -- ссылки не теряем
    return RedirectResponse("/", status_code=301)


@app.get("/robots.txt", response_class=PlainTextResponse)
def robots_txt() -> PlainTextResponse:
    return PlainTextResponse(seo.robots())


@app.get("/sitemap.xml")
def sitemap_xml() -> Response:
    return Response(seo.sitemap(), media_type="application/xml")


def _landing_route(slug: str):
    def landing() -> HTMLResponse:
        return HTMLResponse(page_stamp(seo.render_landing(seo.BY_SLUG[slug])),
                            headers={"Cache-Control": "public, max-age=3600"})
    landing.__name__ = f"landing_{slug.replace('-', '_')}"
    return landing


for _slug in seo.BY_SLUG:
    app.get(f"/{_slug}", response_class=HTMLResponse)(_landing_route(_slug))


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
    storage.give_welcome(user.id, billing.WELCOME_CREDITS)

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


# ------------------------------------------------ вход через Яндекс ID и VK ID

oauth_signer = URLSafeSerializer(SECRET, salt="oauth")


def oauth_redirect_uri(request: Request, provider: str) -> str:
    """Адрес возврата -- ровно тот, что вписан в кабинетах Яндекса и VK:
    на боевом домене всегда https://naslux.ru/..., даже если прокси не
    передал схему; на тестовом -- как пришёл запрос."""
    site = seo.site_url()
    base = site if request.url.hostname == urllib.parse.urlparse(site).hostname \
        else str(request.base_url).rstrip("/")
    return f"{base}/api/auth/{provider}/callback"


@app.get("/api/auth/{provider}/start")
def api_oauth_start(provider: str, request: Request, next: str = "/library"):  # noqa: A002
    if not oauth.configured().get(provider):
        raise HTTPException(404, "Такой вход не подключён")
    redirect_uri = oauth_redirect_uri(request, provider)
    url, remembered = oauth.start(provider, redirect_uri)
    response = RedirectResponse(url, status_code=302)
    response.set_cookie("oauth", oauth_signer.dumps({**remembered, "next": safe_next(next)}),
                        max_age=600, httponly=True, samesite="lax", secure=request.url.scheme == "https")
    return response


@app.get("/api/auth/{provider}/callback")
def api_oauth_callback(provider: str, request: Request):
    """Вернулись от Яндекса или VK: находим или заводим учётную запись.

    Порядок: уже привязанный id сервиса -> та же почта (привязываем) ->
    новая запись из текущей анонимной (её треки остаются при ней)."""
    if not oauth.configured().get(provider):
        raise HTTPException(404, "Такой вход не подключён")
    try:
        remembered = oauth_signer.loads(request.cookies.get("oauth", ""))
    except BadSignature:
        remembered = {}
    redirect_uri = oauth_redirect_uri(request, provider)
    try:
        person = oauth.finish(provider, dict(request.query_params), remembered, redirect_uri)
    except Exception as error:  # noqa: BLE001 -- любая неудача -> понятное сообщение
        return RedirectResponse(f"/account?oauth_error={urllib.parse.quote(str(error)[:200])}", 302)

    visitor = current_user(request)
    account = storage.user_by_provider(provider, person["id"])
    if account is None and person["email"]:
        account = storage.user_by_email(person["email"])
    if account is None:
        if visitor.registered:
            account = visitor                 # вошедший привязывает ещё один способ входа
        else:
            email = person["email"] or f"{provider}-{person['id']}@{provider}.id"
            storage.register(visitor.id, email, "oauth$" + secrets.token_hex(16))
            storage.give_welcome(visitor.id, billing.WELCOME_CREDITS)
            account = storage.user(visitor.id)
    storage.link_provider(account.id, provider, person["id"])
    if visitor.id != account.id and not visitor.registered:
        storage.move_jobs(visitor.id, account.id)
        storage.delete_user_if_empty(visitor.id)
    response = RedirectResponse(remembered.get("next") or "/library", 302)
    response.delete_cookie("oauth")
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
        "studioCreate": f"{studio.SERVICES['create'].price:.0f}",
        "studioMonth": f"{billing.PLANS['studio_month']['price']:.0f}",
        "proMonth": f"{billing.PLANS['pro_month']['price']:.0f}",
        "welcome": str(billing.WELCOME_CREDITS),
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
    payload["oauth"] = oauth.configured()
    payload["isAdmin"] = user.is_admin
    payload["unlimited"] = user.unlimited
    payload["studioCredits"] = user.studio_credits
    # Каталог тарифов -- для профиля: подписки на кредиты и пакеты без подписки
    payload["plans"] = [{"id": key, "title": spec["title"], "price": spec["price"],
                         "studioCredits": spec.get("studio_credits", 0),
                         "subscription": bool(spec.get("subscription"))}
                        for key, spec in billing.PLANS.items() if spec.get("studio_credits")]
    payload["freeChordsPerDay"] = FREE_CHORDS_PER_DAY
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
    suffix = Path(file.filename or "").suffix.lower()
    # Аккорды -- бесплатно и без лимита по деньгам: крючок, ради которого
    # приходят. Платными остаются партии, табы и MIDI (они и нагружают
    # сервер); за них списывается, когда человек их попросит.
    free_chords = not separate_track and suffix not in (".mid", ".midi")
    if free_chords and storage.jobs_today(user.id) >= FREE_CHORDS_PER_DAY and not user.unlimited:
        raise HTTPException(429, f"Сегодня уже {FREE_CHORDS_PER_DAY} бесплатных разборов аккордов — "
                                 "продолжим завтра, или возьмите тариф «Музыкант»")
    access = billing.check_access(user)
    if not free_chords and not access.allowed:
        raise HTTPException(402, access.reason)

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

    if free_chords:
        runner.submit_analysis(job.id, target)
        response = JSONResponse({"jobId": job.id})
        attach_cookie(response, user.id)
        return response

    # Пробная песня списывается в момент постановки в очередь, а не по
    # завершении: иначе один и тот же файл можно было бы гонять бесконечно,
    # обрывая задание на полпути.
    spent = billing.consume(storage, user)
    if not spent:
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
    # charged_kind запоминает, ЧЕМ расплатились -- если разбор в фоне
    # упадёт с ошибкой, _analyze по нему вернёт списанное (см. jobs.py).
    storage.update_job(job.id, counted=True, charged_kind=spent)
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
    """Распознать текст песни по вокальной партии.

    Текст -- платная часть разбора наравне с партиями и табами (см.
    web/static/pricing.html: «В любой [план]: партии, табы, MIDI, текст
    песни»), а не бесплатное дополнение к аккордам. Трек оплачивается один
    раз (_charge_song_once) -- дальше текст, партии и табы того же трека
    уже включены.
    """
    user = current_user(request)
    job = storage.job(job_id)
    if not job or job.user_id != user.id or not job.result:
        raise HTTPException(404, "Разбор ещё не готов")
    if job.result.get("isMidi"):
        # В MIDI нет ни голоса, ни звука вообще -- распознавать нечего, и
        # дальше по коду это читалось бы как "нет дорожки для вокала" и
        # запускало бы разделение на партии прямо на .mid-файле, который
        # Demucs не умеет читать (см. /separate -- там для MIDI такой же
        # отказ, не допускающий до списания).
        raise HTTPException(409, "В MIDI нет вокала — распознавать нечего.")
    ok, why = lyrics_mod.available()
    if not ok:
        raise HTTPException(503, why)
    # Атомарный захват -- та же гонка, что и у /separate (см. claim_job_run):
    # без него два быстрых клика по "Распознать текст" оба читают один и
    # тот же статус "done" и оба попадают в фоновый пул, включая тот
    # случай, когда распознавание само запускает разделение на партии
    # (`_lyrics` -> `_separate_later`) -- тогда гонка идёт уже за общий
    # `out_dir/stems` на диске.
    if not storage.claim_job_run(job_id, allowed_from=("done",)):
        raise HTTPException(409, "Этот трек сейчас обрабатывается")
    # Списание -- как у /separate: если трек ещё не оплачен (job.counted),
    # это первая платная операция над ним и платит она здесь; если уже
    # оплачен разделением или табами другой партии, _charge_song_once
    # вернёт None и текст достанется бесплатно, как и обещано на тарифах.
    try:
        kind = _charge_song_once(user, job)
    except HTTPException:
        storage.update_job(job_id, status="done")
        raise
    if kind:
        storage.update_job(job_id, charged_kind=kind)
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
    if (job.settings or {}).get("kind") == "studio":
        # У задач Студии (web/studio.py) совсем другой формат result --
        # ни "paths", ни "isMidi" в нём нет, и проверки ниже их бы не
        # отсеяли. Без этой проверки запрос перевёл бы уже готовую,
        # оплаченную задачу Студии в "running" (claim_job_run ниже), а
        # _separate_later, не найдя привычных полей, тут же откатил бы её
        # обратно в "done" с чужой по смыслу ошибкой "исходный файл не
        # найден" -- отдельная, понятная проверка здесь честнее.
        raise HTTPException(409, "Это задача Студии — разделить на партии её можно со страницы Студии")
    if not job.result:
        raise HTTPException(409, "Разбор ещё не готов")
    if (job.result.get("paths") or {}).get("parts", {}).keys() - {"full"}:
        raise HTTPException(409, "Трек уже разделён на партии")
    if job.result.get("isMidi"):
        # Только на фронтенде кнопка «Разделить на партии» скрыта для MIDI --
        # прямой запрос к API дошёл бы до _separate_later, а там Demucs не
        # умеет читать .mid и падает уже ПОСЛЕ списания денег, без возврата.
        raise HTTPException(409, "MIDI не на что делить -- в нём уже отдельные партии.")

    ok, why = separate.available()
    if not ok:
        raise HTTPException(503, why)
    # Атомарный захват ДО списания и запуска -- см. docstring claim_job_run.
    # Проверка `job.status` по снимку, прочитанному в начале обработчика,
    # не годится: статус в "running" выставляется только внутри фонового
    # потока, уже ПОСЛЕ того как запрос встал в очередь, так что два
    # быстрых клика оба успевали бы пройти проверку. Захват -- ДО списания,
    # а не после: иначе проигравший гонку за обработку запрос мог бы уже
    # успеть списать деньги и получить 409 вместо результата.
    if not storage.claim_job_run(job_id, allowed_from=("done",)):
        raise HTTPException(409, "Этот трек сейчас обрабатывается")
    try:
        kind = _charge_song_once(user, job)
    except HTTPException:
        storage.update_job(job_id, status="done")
        raise
    # charged_kind запоминает, ЧЕМ расплатились -- если разделение в фоне
    # упадёт с ошибкой, _separate_later по нему вернёт списанное. kind
    # пуст, если трек уже был оплачен раньше (job.counted) -- тогда
    # возвращать при неудаче нечего, это не новое списание.
    if kind:
        storage.update_job(job_id, charged_kind=kind)
    runner.submit_separation(job_id)
    return {"ok": True}


# Застолбить (claim_job_charge), проверить деньги и списать -- три
# отдельных запроса к БД, не одна атомарная операция. Между "застолбили"
# и "откатили обратно, потому что денег не нашлось" есть окно, в котором
# параллельный запрос по ДРУГОЙ партии/табам того же трека видит заявку
# уже застолбленной и трактует это как "трек уже кем-то оплачен" --
# раньше мог проехать бесплатно, хотя первый запрос ещё не подтвердил
# оплату и секундой позже откатится ни с чем (см. findings-2026-10-04).
# Сервис работает одним процессом (`--workers 1`, см. deploy/README.md) --
# значит, рядовой threading.Lock на весь процесс полностью закрывает эту
# гонку: пока первый запрос не доведёт claim до конца (спишет или
# откатит застолбленное), второй просто ждёт лока и видит только
# итоговое, уже непротиворечивое состояние `counted`.
_charge_lock = threading.Lock()


def _charge_song_once(user, job) -> str | None:
    """Трек разобран бесплатно (только аккорды) -- за партии и табы
    списывается один раз, как за обычный разбор; дальше всё включено.

    Разделение на партии и табы по отдельным партиям одного трека могут
    прийти двумя запросами почти одновременно (два клика подряд): оба
    видят один и тот же объект `job` со `counted=False`, снятый до этого
    вызова, и снимок мог устареть. Поэтому право списать застолбливается
    атомарно (claim_job_charge) прежде, чем списывать деньги -- иначе оба
    запроса прошли бы проверку `job.counted` и оплата ушла бы дважды.
    Если списать не удалось (или доступа нет), метка снимается: иначе
    трек остался бы помеченным оплаченным, ничего не списав. `_charge_lock`
    не даёт второму запросу застать этот откат в процессе (см. выше).

    Возвращает, ЧЕМ расплатились (см. billing.consume), если списание
    произошло именно сейчас, или None, если трек уже был оплачен раньше
    (повторный вызов для другой партии того же трека) -- вызывающий
    обязан запомнить непустое значение рядом с тем заданием, которое
    может упасть, чтобы вернуть списанное при неудаче, и не трогать его,
    если ничего нового не списалось."""
    if job.counted:
        return None
    with _charge_lock:
        if not storage.claim_job_charge(job.id):
            return None
        access = billing.check_access(user)
        if not access.allowed:
            storage.update_job(job.id, counted=False)
            raise HTTPException(402, "Аккорды — бесплатно, а партии, табы и MIDI — по тарифу. "
                                     + access.reason)
        spent = billing.consume(storage, user)
        if not spent:
            storage.update_job(job.id, counted=False)
            raise HTTPException(402, "Не получилось списать разбор — обновите страницу и попробуйте снова")
        return spent


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
    kind = _charge_song_once(user, parent)
    child = storage.create_job(
        user.id, f"{parent.filename} — {stem_key}", {"parent": job_id, "stem": stem_key}
    )
    # Деньги списываются с РОДИТЕЛЯ (весь трек оплачивается один раз), а
    # падать при разборе может именно этот новый job табов -- charged_kind
    # кладём на него, чтобы _tabs вернул списанное, только если списание
    # произошло именно сейчас (kind непуст), а не было оплачено раньше
    # отдельным разделением или табами другой партии того же трека.
    if kind:
        storage.update_job(child.id, charged_kind=kind)
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
    return page("studio.html", {"HEAD": seo.head_tags("/studio", STUDIO_TITLE, STUDIO_DESCRIPTION),
                                "FEATURES": seo.features_nav(),
                                "TITLE": STUDIO_TITLE, "DESCRIPTION": STUDIO_DESCRIPTION})


def _first_analysis(result: dict, field: str):
    """Тональность/темп первой разобранной версии -- для строки трека
    (темп, поправленный музыкантом, важнее автоопределения)."""
    for name in [f["name"] for f in result.get("files") or []]:
        value = (((result.get("tempoFix") or {}) if field == "bpm" else {}).get(name)
                 or (result.get("analysis") or {}).get(name) or {}).get(field)
        if value:
            return value
    return None


def _chord_line(result: dict) -> str:
    """Аккорды песни по SheetSage2 одной строкой без повторов: «Em D C D»."""
    quality = {"maj": "", "min": "m", "7": "7", "maj7": "maj7", "min7": "m7", "sus2": "sus2",
               "sus4": "sus4", "dim": "dim", "aug": "aug", "maj6": "6", "min6": "m6", "dim7": "dim7",
               "hdim7": "m7b5", "9": "9", "maj9": "maj9", "min9": "m9"}
    out = []
    for _s, _e, label in ((result.get("sheet") or {}).get("chords") or [])[:400]:
        root, _, rest = str(label).partition(":")
        if not root or root == "N":
            continue
        kind, _, bass = rest.partition("/")
        name = root + quality.get(kind or "maj", kind)
        if not out or out[-1] != name:
            out.append(name)
    return " ".join(out[:64])


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
        "bpm": (result.get("settings") or {}).get("bpm") or _first_analysis(result, "bpm"),
        "key": (result.get("settings") or {}).get("key_scale") or _first_analysis(result, "key"),
        "keys": {name: {"key": a.get("key"), "bpm": ((result.get("tempoFix") or {}).get(name) or a).get("bpm")}
                 for name, a in (result.get("analysis") or {}).items() if not a.get("error")},
        "sourceFile": settings.get("sourceFile", ""),
        "files": files,
        "from": settings.get("from"),
        "keepVocals": bool(settings.get("keepVocals")),
        "style": (settings.get("input") or {}).get("prompt", ""),
        "voice": settings.get("voice", ""),
        "hasSource": any(f.startswith("source.") for f in os.listdir(folder)) if os.path.isdir(folder) else False,
        "midi": [{**m, "url": f"/api/studio/file/{job.id}/{m['name']}"} for m in result.get("midi") or []
                 if os.path.isfile(os.path.join(folder, m["name"]))],
        "archives": [{"name": a, "url": f"/api/studio/file/{job.id}/{a}"} for a in result.get("archives") or []
                     if os.path.isfile(os.path.join(folder, a))],
        "pro": bool(result.get("pro")),
        "midiBusy": [n for j, n in studio_runner.transcribing if j == job.id],
        "extras": [{**x, "url": f"/api/studio/file/{job.id}/{x['name']}"} for x in result.get("extras") or []
                   if os.path.isfile(os.path.join(folder, x["name"]))],
        "shift": {"of": settings.get("shiftOf"), "file": settings.get("shiftFile"),
                  "semitones": settings.get("semitones", 0), "tempo": settings.get("tempo", 1.0)}
        if settings.get("mode") == "shift" else None,
        "cover": f"/api/studio/file/{job.id}/cover.jpg"
                 if os.path.isfile(os.path.join(folder, "cover.jpg")) else None,
        "lyrics": result.get("lyrics") or (settings.get("input") or {}).get("lyrics") or "",
        "hasAbc": bool(result.get("abc")),
        "chords": _chord_line(result),
        # Файлы Студии уборка удаляет через 14 дней (deploy/cleanup.py)
        "expired": job.status == "done" and not files and not result.get("midi"),
    }


@app.get("/api/studio")
def api_studio(request: Request):
    user = current_user(request)
    ready, why = studio.available()
    response = JSONResponse({
        "ready": ready, "why": why,
        "services": {k: {"title": v.title, "price": v.price, "pack": studio.credit_cost(k),
                         "packKeep": studio.credit_cost(k, True)}
                     for k, v in studio.SERVICES.items()},
        "createOpen": studio.create_open(),
        "presets": {k: v[0] for k, v in studio.PRESETS.items()},
        "tracks": studio.TRACKS,
        "voices": {k: v[0] for k, v in studio.VOICES.items()},
        "myVoices": [{"id": v["id"], "name": v["name"]} for v in storage.voices(user.id)],
        "voiceCloneOpen": studio.voice_clone_open(),
        "balance": user.balance, "unlimited": user.unlimited, "registered": user.registered,
        "studioCredits": user.studio_credits,
        "restyleOpen": studio.restyle_open() or user.unlimited,
        "restyleEngine": studio.restyle_engine(),
        "yue2Open": studio.yue2_open(user),
        "email": user.email, "maxMb": MAX_UPLOAD_MB, "maxSeconds": studio.MAX_SECONDS,
        "jobs": [_studio_job_payload(j) for j in storage.studio_jobs(user.id)],
    })
    attach_cookie(response, user.id)
    return response


@app.post("/api/studio")
async def api_studio_start(
    request: Request,
    file: UploadFile | None = File(None),
    mode: str = Form("restyle"),
    title: str = Form(""),
    preset: str = Form("numetal"),
    prompt: str = Form(""),
    lyrics: str = Form(""),
    audio_influence: float = Form(0.5),
    style_influence: float = Form(0.5),
    weirdness: float = Form(0.3),
    melody: float = Form(0.0),
    track: str = Form("drums"),
    language: str = Form("ru"),
    voice: str = Form(""),
    keep_vocals: bool = Form(False),
    again: str = Form(""),
    againFile: str = Form(""),
    rights: str = Form(""),
    reference: bool = Form(False),
    pro: bool = Form(False),
    engine: str = Form(""),
    closeness: str = Form("melody"),
    cfg_scale: float = Form(1.0),
    creativity: float = Form(0.5),
):
    user = current_user(request)
    ready, why = studio.available()
    if not ready:
        raise HTTPException(503, why)
    service = studio.SERVICES.get(mode)
    if service is None:
        raise HTTPException(400, "Неизвестная услуга")
    pro = pro and mode == "stems"
    asked = engine
    engine = studio.restyle_engine() if mode in ("restyle", "create") or pro else "runpod"
    if asked == "yue2" and mode in ("restyle", "create") and studio.yue2_open(user):
        engine = "yue2"            # проба владельца, см. studio.YUE2_NAME
    if pro and engine != "mureka":
        raise HTTPException(503, "Глубокое разделение пока не подключено")
    if pro:
        service = studio.SERVICES["stems_pro"]
    if mode == "create" and engine not in ("mureka", "yue2") and not studio.ace_create_open():
        raise HTTPException(503, "Песни с нуля пишет Mureka, а она на сервере не подключена")
    if reference and mode == "create" and engine != "mureka":
        raise HTTPException(503, "Песня «как в образце» временно недоступна — опишите стиль словами")
    if mode == "restyle" and not (studio.restyle_open() or user.unlimited):
        raise HTTPException(409, "Переделка в другой стиль переезжает на новый движок и скоро "
                                 "вернётся. Разделение на партии и дописывание партии работают.")
    if engine == "runpod" and not studio.api_key():
        raise HTTPException(503, "Эта услуга ещё не подключена: нет ключа RunPod на сервере")
    keep_vocals = keep_vocals and mode == "restyle" and engine == "mureka"
    if mode == "create" and not (prompt.strip() or lyrics.strip() or preset in studio.PRESETS):
        raise HTTPException(400, "Опишите стиль или добавьте текст песни")
    # «Повторить»: исходник берётся из прошлой работы, загружать заново не нужно.
    again_source = None
    if file is None and again and mode != "create":
        previous = storage.job(again)
        if previous and previous.user_id == user.id and (previous.settings or {}).get("kind") == "studio":
            folder_before = studio_runner.folder(again)
            versions = [f["name"] for f in (previous.result or {}).get("files") or []]
            if againFile in versions and os.path.isfile(os.path.join(folder_before, againFile)):
                # «Кавер на этот трек»: исходник -- выбранная версия самого трека
                again_source = os.path.join(folder_before, againFile)
            else:
                again_source = next((os.path.join(folder_before, f) for f in sorted(os.listdir(folder_before))
                                     if f.startswith("source.")), None) if os.path.isdir(folder_before) else None
        if not again_source:
            raise HTTPException(409, "Исходник прошлой работы уже удалён — загрузите трек заново")
    suffix = Path((file.filename if file else again_source or "") or "").suffix.lower()
    if (mode != "create" or reference) and ((file is None and not again_source) or suffix not in ALLOWED
                                            or suffix in (".mid", ".midi")):
        raise HTTPException(400, "Нужен аудиофайл: mp3, wav, flac, ogg, m4a")
    reference = reference and mode == "create" and file is not None
    vocal_id = ""
    if voice.startswith("my:"):
        vocal_id = next((v["vocal_id"] for v in storage.voices(user.id) if v["id"] == voice[3:]), "")
        if not vocal_id:
            raise HTTPException(404, "Такого голоса нет — выберите другой")
    if (mode != "create" or reference) and rights not in ("own", "cover"):
        # Оферта, п. 6.3: перед работой с записью человек отмечает, чья это
        # музыка, и соглашается с условиями -- отметка хранится с заказом.
        raise HTTPException(400, "Отметьте, чья это музыка, и согласитесь с условиями")
    if mode == "enrich" and track not in studio.TRACKS:
        raise HTTPException(400, "Выберите партию, которую дописать")
    style = prompt.strip()[:500] or (studio.PRESETS[preset][1] if preset in studio.PRESETS else "")
    if mode in ("restyle", "enrich") and not style:
        raise HTTPException(400, "Опишите стиль: жанр, настроение, инструменты")
    if engine in ("runpod", "yue2") and mode in ("create", "restyle") and voice in studio.VOICES:
        # У ACE-Step нет переключателя голоса -- просим словами в стиле
        style = ", ".join(p for p in (style, studio.VOICES[voice][1]) if p)[:1024]
    cost_key = "stems_pro" if pro else mode
    by_pack = user.studio_credits >= studio.credit_cost(cost_key, keep_vocals) > 0
    if not user.unlimited and not by_pack and user.balance < service.price:
        raise HTTPException(402, f"{service.title} стоит {service.price:.0f} ₽, на балансе "
                                 f"{user.balance:.0f} ₽. Пополните баланс или возьмите пакет "
                                 "генераций на странице тарифов.")

    clamp = lambda v: max(0.0, min(1.0, float(v)))  # noqa: E731
    knobs = {"audio_influence": clamp(audio_influence),
             "style_influence": clamp(style_influence), "weirdness": clamp(weirdness)}
    voice = voice if voice in studio.VOICES or vocal_id else ""
    settings = {"kind": "studio", "mode": mode, "title": service.title, "preset": preset,
                **knobs, "track": track, "charged": 0, "engine": engine, "voice": voice,
                "keepVocals": keep_vocals,
                **({"rights": {"kind": rights, "at": time.time(),
                               "ip": request.client.host if request.client else ""}}
                   if mode != "create" or reference else {}),
                **({"reference": True} if reference else {})}
    if mode == "create":   # у песни с нуля файл -- лишь образец стиля, имя -- название
        name = title.strip()[:80] or "Новая песня"
    else:
        name = (file.filename if file else "") or (storage.job(again).filename if again_source else "") \
            or title.strip()[:80] or studio.PRESETS.get(preset, ("Песня",))[0]
    job = storage.create_job(user.id, name, settings)
    folder = studio_runner.folder(job.id)
    os.makedirs(folder, exist_ok=True)
    source = os.path.join(folder, f"source{suffix}")
    size, limit = 0, MAX_UPLOAD_MB * 1024 * 1024
    if again_source:
        shutil.copyfile(again_source, source)
    with open(source, "wb") if file else open(os.devnull, "wb") as out:
        while file and (chunk := await file.read(1024 * 1024)):
            size += len(chunk)
            if size > limit:
                out.close()
                shutil.rmtree(folder, ignore_errors=True)
                storage.update_job(job.id, status="error", error="Файл слишком большой")
                raise HTTPException(413, f"Файл больше {MAX_UPLOAD_MB} МБ")
            out.write(chunk)

    _studio_charge_and_submit(request, user, job.id, service, folder, {
        "mode": mode, "prompt": style, "lyrics": studio.english_tags(lyrics.strip())[:5000], **knobs,
        # Две версии за раз: авторы ACE-Step советуют выбирать из
        # нескольких, а GPU на вторую тратит секунды.
        "variants": 2,
        **(studio.runpod_restyle_recipe(knobs["audio_influence"], clamp(melody))
           if mode == "restyle" and engine == "runpod" else {}),
        "track": track, "language": language if language in ("ru", "en") else "ru",
        "voice": "" if vocal_id else voice, "keep_vocals": keep_vocals, "cost_key": cost_key,
        **({"vocal_id": vocal_id} if vocal_id else {}), **({"reference": True} if reference else {}),
        # Настройки YuE2: близость к оригиналу, сила стиля, смелость
        **({"closeness": closeness if closeness in ("full", "melody", "free") else "melody",
            "cfg_scale": max(0.8, min(1.8, float(cfg_scale))), "creativity": clamp(creativity)}
           if engine == "yue2" else {}),
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
    cost = studio.credit_cost(worker_input.get("cost_key") or worker_input["mode"],
                              bool(worker_input.get("keep_vocals")))
    if not user.unlimited and cost and storage.spend_studio_credit(user.id, cost):
        # Генерации из пакета: деньги не трогаем, при сбое вернём их в пакет.
        settings["charged_credit"] = cost
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
        # Ужатый под Mureka трек -- его забирает ретранслятор (relay/).
        "mureka_url": studio.link(base, SECRET, job_id, "mureka"),
    })


@app.post("/api/studio/{job_id}/stems")
def api_studio_split_result(job_id: str, request: Request, file: str = Form(""),
                            pro: bool = Form(False)):
    """Разделить на партии уже готовую переделку -- без повторной загрузки;
    pro -- глубокое разделение Mureka с MIDI."""
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
    cost_key = "stems_pro" if pro else "stems"
    if pro and studio.restyle_engine() != "mureka":
        raise HTTPException(503, "Глубокое разделение пока не подключено")
    service = studio.SERVICES[cost_key]
    if not user.unlimited and user.balance < service.price \
            and user.studio_credits < studio.credit_cost(cost_key):
        raise HTTPException(402, f"{service.title} стоит {studio.credit_cost(cost_key)} кредитов или "
                                 f"{service.price:.0f} ₽. Пополните баланс на странице тарифов.")
    title = f"{(parent.settings or {}).get('title', '')}: {parent.filename}"
    variant = studio.label_of((parent.settings or {}).get("mode", ""), files[0]["name"])
    job = storage.create_job(user.id, parent.filename, {
        "kind": "studio", "mode": "stems", "title": service.title, "from": job_id,
        "variant": variant, "sourceFile": files[0]["name"], "charged": 0,
        **({"engine": "mureka"} if pro else {})})
    folder = studio_runner.folder(job.id)
    os.makedirs(folder, exist_ok=True)
    shutil.copyfile(os.path.join(studio_runner.folder(job_id), files[0]["name"]),
                    os.path.join(folder, "source.mp3"))
    _studio_charge_and_submit(request, user, job.id, service, folder,
                              {"mode": "stems", "cost_key": cost_key})
    return {"jobId": job.id, "from": title}


@app.post("/api/studio/{job_id}/notes")
def api_studio_notes(job_id: str, request: Request, file: str = Form("")):
    """«Ноты, аккорды и MIDI» версии трека: SheetSage2 снимает мелодию
    вокала и инструментов, аккорды, тональность и части песни. Проба
    владельца (веса CC BY-NC 4.0, см. studio.YUE2_NAME), бесплатно."""
    user = current_user(request)
    if not studio.yue2_open(user):
        raise HTTPException(403, "Ноты пока в пробе — доступны только владельцу сайта")
    parent = storage.job(job_id)
    if (not parent or parent.user_id != user.id
            or (parent.settings or {}).get("kind") != "studio" or parent.status != "done"):
        raise HTTPException(404, "Готовая работа не найдена")
    files = [f for f in (parent.result or {}).get("files") or []
             if os.path.isfile(os.path.join(studio_runner.folder(job_id), f["name"]))
             and (not file or f["name"] == file)]
    if not files:
        raise HTTPException(409, "Файлы этой работы уже удалены по сроку хранения")
    variant = studio.label_of((parent.settings or {}).get("mode", ""), files[0]["name"])
    job = storage.create_job(user.id, parent.filename, {
        "kind": "studio", "mode": "notes", "title": "Ноты, аккорды и MIDI", "from": job_id,
        "variant": variant, "sourceFile": files[0]["name"], "charged": 0, "engine": "yue2"})
    folder = studio_runner.folder(job.id)
    os.makedirs(folder, exist_ok=True)
    shutil.copyfile(os.path.join(studio_runner.folder(job_id), files[0]["name"]),
                    os.path.join(folder, "source" + (Path(files[0]["name"]).suffix or ".mp3")))
    _studio_charge_and_submit(request, user, job.id, studio.SERVICES["stems"], folder,
                              {"mode": "notes", "cost_key": "notes"})
    return {"jobId": job.id}


@app.get("/api/studio/{job_id}/abc")
def api_studio_abc(job_id: str, request: Request):
    """Ноты работы в ABC -- для нотного стана в браузере (abcjs)."""
    user = current_user(request)
    job = storage.job(job_id)
    if not job or job.user_id != user.id or not (job.result or {}).get("abc"):
        raise HTTPException(404, "Нот у этой работы нет")
    return {"abc": job.result["abc"], "name": job.filename,
            "key": ((job.result.get("sheet") or {}).get("key") or [[0, 0, ""]])[0][2]}


UPLOADS_PER_DAY = 40


@app.post("/api/studio/upload")
async def api_studio_upload(request: Request, file: UploadFile = File(...), rights: str = Form("")):
    """Свой трек -- сразу в «Мои треки», бесплатно: слушать, узнать тональность
    и темп, сдвинуть их, открыть в мультитреке, разделить на партии."""
    user = current_user(request)
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in ALLOWED or suffix in (".mid", ".midi"):
        raise HTTPException(400, "Нужен аудиофайл: mp3, wav, flac, ogg, m4a")
    if rights not in ("own", "cover"):
        raise HTTPException(400, "Отметьте, чья это музыка, и согласитесь с условиями")
    if storage.studio_uploads_today(user.id) >= UPLOADS_PER_DAY:
        raise HTTPException(429, f"За сутки можно загрузить {UPLOADS_PER_DAY} треков — продолжим завтра")
    job = storage.create_job(user.id, (file.filename or "Трек")[:120], {
        "kind": "studio", "mode": "upload", "title": "Загруженный трек", "engine": "local", "charged": 0,
        "rights": {"kind": rights, "at": time.time(), "ip": request.client.host if request.client else ""}})
    folder = studio_runner.folder(job.id)
    os.makedirs(folder, exist_ok=True)
    size, limit = 0, MAX_UPLOAD_MB * 1024 * 1024
    with open(os.path.join(folder, f"source{suffix}"), "wb") as out:
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            if size > limit:
                out.close()
                shutil.rmtree(folder, ignore_errors=True)
                storage.delete_job(job.id)
                raise HTTPException(413, f"Файл больше {MAX_UPLOAD_MB} МБ")
            out.write(chunk)
    studio_runner.submit(job.id, {"mode": "upload"})
    response = JSONResponse({"jobId": job.id})
    attach_cookie(response, user.id)
    return response


@app.get("/api/studio/{job_id}/analysis")
def api_studio_analysis(job_id: str, request: Request, file: str = ""):
    """Тональность, темп, доли и аккорды версии. Первый запрос запускает
    разбор (секунды) и отвечает pending -- страница спрашивает ещё раз."""
    user = current_user(request)
    job = storage.job(job_id)
    if (not job or job.user_id != user.id or (job.settings or {}).get("kind") != "studio"
            or job.status != "done"):
        raise HTTPException(404, "Готовая работа не найдена")
    names = [f["name"] for f in (job.result or {}).get("files") or []]
    folder = studio_runner.folder(job_id)
    if file == "harmony" and (job.settings or {}).get("mode") == "stems":
        # Аккорды по партиям без голоса и барабанов: мелодия голоса и
        # тарелки сбивают сеть сильнее всего (как «без барабанов и голоса»
        # в обычном разборе). Сведение -- один раз, дальше -- кеш.
        target = os.path.join(folder, "harmony.wav")
        if not os.path.isfile(target):
            parts = {("vocals" if re.search(r"vocal|вокал", n, re.I) else
                      "drums" if re.search(r"drum|барабан", n, re.I) else n): os.path.join(folder, n)
                     for n in names if os.path.isfile(os.path.join(folder, n))}
            if not separate.harmonic_mix(parts, target):
                raise HTTPException(409, "Не получилось свести партии без голоса и барабанов")
        file = "harmony.wav"
        names.append(file)
    elif file == "source":
        # Партии без трека-родителя: доли и аккорды -- по исходнику целиком
        file = next((f for f in sorted(os.listdir(folder)) if f.startswith("source.")), "") \
            if os.path.isdir(folder) else ""
        names.append(file)
    if not file or file not in names or not os.path.isfile(os.path.join(folder, file)):
        raise HTTPException(409, "Файлы этой работы уже удалены по сроку хранения")
    cached = ((job.result or {}).get("analysis") or {}).get(file)
    if cached and cached.get("v") == studio.ANALYSIS_VERSION:
        owner, name = _tempo_owner(job, file)
        return studio.with_tempo(cached, ((owner.result or {}).get("tempoFix") or {}).get(name))
    studio_runner.analyze_later(job_id, file)
    return {"pending": True}


def _tempo_owner(job, file: str):
    """Где хранится поправленный темп: у партий -- на исходном треке, чтобы
    Студия, мультитрек и метроном показывали одно и то же."""
    settings = job.settings or {}
    if file == "harmony.wav" and settings.get("from") and settings.get("sourceFile"):
        parent = storage.job(settings["from"])
        if parent and parent.user_id == job.user_id:
            return parent, settings["sourceFile"]
    return job, file


@app.post("/api/studio/{job_id}/tempo")
def api_studio_tempo(job_id: str, request: Request, file: str = Form(""), bpm: float = Form(0),
                     start: float = Form(0)):
    """Свой темп и сильная доля вместо автоопределения; bpm=0 -- вернуть авто."""
    user = current_user(request)
    job = storage.job(job_id)
    if (not job or job.user_id != user.id or (job.settings or {}).get("kind") != "studio"
            or job.status != "done"):
        raise HTTPException(404, "Готовая работа не найдена")
    if bpm and not 30 <= bpm <= 300:
        raise HTTPException(400, "Темп — от 30 до 300 ударов в минуту")
    names = [f["name"] for f in (job.result or {}).get("files") or []]
    if file == "harmony":
        file = "harmony.wav"
    elif file == "source" or file not in names + ["harmony.wav"]:
        raise HTTPException(400, "Неизвестная версия трека")
    owner, name = _tempo_owner(job, file)
    with studio_runner._analysis_lock:
        fresh = storage.job(owner.id)
        result = dict(fresh.result or {})
        fixes = dict(result.get("tempoFix") or {})
        if bpm:
            fixes[name] = {"bpm": round(bpm, 2), "start": round(max(0.0, start), 3)}
        else:
            fixes.pop(name, None)
        result["tempoFix"] = fixes
        storage.update_job(owner.id, result=result)
    return {"ok": True}


@app.post("/api/studio/{job_id}/reference")
def api_studio_reference(job_id: str, request: Request, file: str = Form(""), sheet: str = Form("")):
    """Эталон аккордов песни (вставленный лист с сайта аккордов) -- сверка
    разбора всеми моделями. Текст песни сразу отбрасывается: храним только
    аккорды по порядку."""
    from . import benchmark as bench

    user = current_user(request)
    job = storage.job(job_id)
    if (not job or job.user_id != user.id or (job.settings or {}).get("kind") != "studio"
            or job.status != "done"):
        raise HTTPException(404, "Готовая работа не найдена")
    names = [f["name"] for f in (job.result or {}).get("files") or []]
    file = file or (names[0] if names else "")
    if file not in names or not os.path.isfile(os.path.join(studio_runner.folder(job_id), file)):
        raise HTTPException(409, "Файлы этой работы уже удалены по сроку хранения")
    chords = bench.parse_sheet(sheet)
    if len(chords) < 3:
        raise HTTPException(400, "Не нашли аккордов — вставьте лист с аккордами над строками, как на сайте аккордов")
    with studio_runner._analysis_lock:
        fresh = storage.job(job_id)
        result = dict(fresh.result or {})
        result["reference"] = {**(result.get("reference") or {}),
                               file: {"chords": chords[:2000], "at": time.time(), "pending": True}}
        storage.update_job(job_id, result=result)
    studio_runner.benchmark_later(job_id, file)
    return {"pending": True, "chords": len(chords)}


@app.get("/api/studio/{job_id}/reference")
def api_studio_reference_get(job_id: str, request: Request, file: str = ""):
    user = current_user(request)
    job = storage.job(job_id)
    if not job or job.user_id != user.id:
        raise HTTPException(404, "Готовая работа не найдена")
    names = [f["name"] for f in (job.result or {}).get("files") or []]
    file = file or (names[0] if names else "")
    entry = ((job.result or {}).get("reference") or {}).get(file)
    if not entry:
        raise HTTPException(404, "Эталона ещё нет")
    if entry.get("pending") and not studio_runner.benchmarking(job_id, file):
        studio_runner.benchmark_later(job_id, file)      # оборвал перезапуск сайта
    return {k: v for k, v in entry.items() if k != "chords"} | {"chords": len(entry.get("chords") or [])}


@app.post("/api/studio/benchmark/rerun")
def api_studio_benchmark_rerun(request: Request):
    """Пересверить все эталоны человека -- после правки моделей, без
    повторной вставки листов. Сверки идут по одной (studio.HEAVY)."""
    user = current_user(request)
    queued = 0
    for job in storage.user_jobs(user.id, limit=500):
        for file, entry in ((job.result or {}).get("reference") or {}).items():
            # «Ждёт» без живой сверки -- её оборвал перезапуск сайта: ставим заново
            if studio_runner.benchmarking(job.id, file) \
                    or not os.path.isfile(os.path.join(studio_runner.folder(job.id), file)):
                continue
            with studio_runner._analysis_lock:
                fresh = storage.job(job.id)
                result = dict(fresh.result or {})
                refs = dict(result.get("reference") or {})
                refs[file] = {**refs[file], "pending": True}
                result["reference"] = refs
                storage.update_job(job.id, result=result)
            studio_runner.benchmark_later(job.id, file)
            queued += 1
    return {"queued": queued}


@app.get("/api/studio/benchmark")
def api_studio_benchmark(request: Request):
    """Все эталоны человека: итог каждой модели по песням и в среднем."""
    user = current_user(request)
    rows, totals = [], {}
    for job in storage.user_jobs(user.id, limit=500):
        for file, entry in ((job.result or {}).get("reference") or {}).items():
            scores = entry.get("scores") or {}
            rows.append({"id": job.id, "name": job.filename, "file": file, "pending": entry.get("pending"),
                         "error": entry.get("error"), "scores": scores})
            for model, score in scores.items():
                totals.setdefault(model, []).append(score["score"])
    average = {m: round(sum(v) / len(v), 3) for m, v in totals.items()}
    return {"rows": rows, "average": average}


DESCRIBE_PER_DAY = 30
_describe_calls: dict[str, list[float]] = {}


@app.get("/api/studio/{job_id}/describe")
def api_studio_describe(job_id: str, request: Request, file: str = ""):
    """Стиль и слова готового трека (Mureka song/describe + recognize) -- для
    «Повторить» и «Кавер на этот трек». Первый запрос запускает разбор в фоне
    и отвечает pending; результат хранится при треке."""
    user = current_user(request)
    job = storage.job(job_id)
    if (not job or job.user_id != user.id or (job.settings or {}).get("kind") != "studio"
            or job.status != "done"):
        raise HTTPException(404, "Готовая работа не найдена")
    names = [f["name"] for f in (job.result or {}).get("files") or []]
    file = file or (names[0] if names else "")
    if file not in names or not os.path.isfile(os.path.join(studio_runner.folder(job_id), file)):
        raise HTTPException(409, "Файлы этой работы уже удалены по сроку хранения")
    cached = ((job.result or {}).get("described") or {}).get(file)
    if cached and not cached.get("error"):
        return cached
    if studio.restyle_engine() != "mureka":
        raise HTTPException(503, "Распознавание стиля временно недоступно")
    now = time.time()
    recent = [t for t in _describe_calls.get(user.id, []) if now - t < 86400]
    if len(recent) >= DESCRIBE_PER_DAY and not user.unlimited:
        raise HTTPException(429, "На сегодня распознаваний достаточно — попробуйте завтра")
    base = str(request.base_url).rstrip("/")
    if studio_runner.describe_later(job_id, file, studio.link(base, SECRET, job_id, "mureka")):
        _describe_calls[user.id] = [*recent, now]
    return {"pending": True}


DRUMS = re.compile(r"drum|барабан|ударн", re.IGNORECASE)


@app.post("/api/studio/{job_id}/midi")
def api_studio_midi(job_id: str, request: Request, file: str = Form("")):
    """MIDI уже готовой партии -- без повторного разделения, бесплатно:
    наша расшифровка нот (та же, что для табов). Барабаны так не
    расшифровать -- для них глубокое разделение PRO."""
    user = current_user(request)
    job = storage.job(job_id)
    if (not job or job.user_id != user.id or (job.settings or {}).get("kind") != "studio"
            or job.status != "done"):
        raise HTTPException(404, "Готовая работа не найдена")
    names = [f["name"] for f in (job.result or {}).get("files") or []]
    if file not in names or not os.path.isfile(os.path.join(studio_runner.folder(job_id), file)):
        raise HTTPException(409, "Файлы этой работы уже удалены по сроку хранения")
    label = studio.label_of((job.settings or {}).get("mode", ""), file)
    if DRUMS.search(file) or DRUMS.search(label):
        raise HTTPException(400, "Барабаны в MIDI переводит только «Глубокое разделение + MIDI» — "
                                 "обычная расшифровка слышит ноты, а не удары")
    ready, why = audioin.available()
    if not ready:
        raise HTTPException(503, why)
    studio_runner.transcribe_later(job_id, file)
    return {"pending": True}


@app.get("/studio/mix/{job_id}", response_class=HTMLResponse)
def studio_mix_page(job_id: str) -> HTMLResponse:
    return page("mix.html")


@app.post("/api/studio/{job_id}/shift")
def api_studio_shift(job_id: str, request: Request, file: str = Form(""),
                     semitones: int = Form(0), tempo: float = Form(100)):
    """Темп и тональность любой готовой версии -- на своём сервере, бесплатно."""
    user = current_user(request)
    parent = storage.job(job_id)
    if (not parent or parent.user_id != user.id
            or (parent.settings or {}).get("kind") != "studio" or parent.status != "done"):
        raise HTTPException(404, "Готовая работа не найдена")
    parent_files = (parent.result or {}).get("files") or []
    names = [f["name"] for f in parent_files]
    # file="*" -- все дорожки разом (мультитрек): партии звучат вместе,
    # сдвигать их по одной нельзя
    batch = names if file == "*" else [file]
    sources = [os.path.join(studio_runner.folder(job_id), n) for n in batch]
    if not batch or any(n not in names for n in batch) or not all(os.path.isfile(x) for x in sources):
        raise HTTPException(409, "Файлы этой работы уже удалены по сроку хранения")
    semitones = max(-12, min(12, int(semitones)))
    rate = max(50.0, min(150.0, float(tempo))) / 100
    if semitones == 0 and round(rate * 100) == 100:
        raise HTTPException(400, "Сдвиньте тон или темп")
    # Сдвиг партии -- к её треку, а не к работе «Партии»
    owner = (parent.settings or {}).get("from") or job_id
    what = "Все дорожки" if file == "*" else studio.label_of((parent.settings or {}).get("mode", ""), file)
    job = storage.create_job(user.id, parent.filename, {
        "kind": "studio", "mode": "shift", "title": "Темп и тональность", "from": owner,
        "variant": f"{what} · {studio.shift_label(semitones, rate)}",
        "engine": "local", "semitones": semitones, "tempo": rate, "charged": 0,
        "shiftOf": job_id, "shiftFile": file,
        **({"batch": [{"name": f["name"], "label": f.get("label") or f["name"]} for f in parent_files]}
           if file == "*" else {})})
    folder = studio_runner.folder(job.id)
    os.makedirs(folder, exist_ok=True)
    if file == "*":
        for name, src in zip(batch, sources):
            shutil.copyfile(src, os.path.join(folder, f"src_{name}"))
    else:
        shutil.copyfile(sources[0], os.path.join(folder, "source.mp3"))
    studio_runner.submit(job.id, {"mode": "shift"})
    return {"jobId": job.id}


@app.post("/api/studio/{job_id}/tabs")
def api_studio_to_tabs(job_id: str, request: Request, file: str = Form("")):
    """Готовую песню или партию Студии -- в обычный разбор NASLUX: табы, аккорды, MIDI."""
    user = current_user(request)
    parent = storage.job(job_id)
    if (not parent or parent.user_id != user.id
            or (parent.settings or {}).get("kind") != "studio" or parent.status != "done"):
        raise HTTPException(404, "Готовая работа не найдена")
    names = [f["name"] for f in ((parent.result or {}).get("files") or [])
             + ((parent.result or {}).get("midi") or [])]
    source = os.path.join(studio_runner.folder(job_id), file)
    if file not in names or not os.path.isfile(source):
        raise HTTPException(409, "Файлы этой работы уже удалены по сроку хранения")
    access = billing.check_access(user)
    if not access.allowed:
        raise HTTPException(402, access.reason)
    label = studio.label_of((parent.settings or {}).get("mode", ""), file)
    whole = (parent.settings or {}).get("mode") != "stems" and not file.endswith(".mid")
    job = storage.create_job(user.id, f"{parent.filename} — {label}", {
        "capo": 0, "tempo": 0, "minChord": 0.9,
        "vocabulary": audiochords.DEFAULT_VOCABULARY, "separate": whole,
        "model": separate.DEFAULT_MODEL, "quality": separate.DEFAULT_QUALITY,
        "removeGhosts": True, "maxPolyphony": 0, "studio": job_id})
    upload_dir = os.path.join(DATA_DIR, "uploads", job.id)
    os.makedirs(upload_dir, exist_ok=True)
    target = os.path.join(upload_dir, f"{safe_stem(file)}{Path(file).suffix.lower()}")
    shutil.copyfile(source, target)
    spent = billing.consume(storage, user)
    if not spent:
        shutil.rmtree(upload_dir, ignore_errors=True)
        storage.update_job(job.id, status="error", error="Лимит разборов уже израсходован")
        raise HTTPException(402, "Лимит разборов уже израсходован — пополните баланс на странице тарифов.")
    storage.update_job(job.id, counted=True, charged_kind=spent)
    runner.submit_analysis(job.id, target)
    return {"jobId": job.id}


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


@app.get("/api/studio/mureka/{job_id}")
def api_studio_mureka_source(job_id: str, e: int = 0, s: str = ""):
    """Трек, ужатый под условия Mureka (mp3 до 10 МБ и 350 с), -- для ретранслятора."""
    _studio_link_job(job_id, "mureka", e, s)
    path = os.path.join(studio_runner.folder(job_id), "for_mureka.mp3")
    if not os.path.isfile(path):
        raise HTTPException(404, "Трек ещё не подготовлен")
    return FileResponse(path, media_type="audio/mpeg")


_lyrics_calls: dict[str, list[float]] = {}


@app.post("/api/studio/lyrics")
def api_studio_lyrics(request: Request, prompt: str = Form(...)):
    """Сочинить текст песни по описанию (Mureka lyrics/generate). Бесплатно:
    стоит нам доли цента, -- но не больше LYRICS_PER_DAY раз в сутки."""
    user = current_user(request)
    if studio.restyle_engine() != "mureka":
        raise HTTPException(503, "Сочинение текста временно недоступно")
    if not prompt.strip():
        raise HTTPException(400, "Опишите, о чём песня")
    now = time.time()
    recent = [t for t in _lyrics_calls.get(user.id, []) if now - t < 86400]
    if len(recent) >= studio.LYRICS_PER_DAY and not user.unlimited:
        raise HTTPException(429, "На сегодня текстов достаточно — попробуйте завтра")
    _lyrics_calls[user.id] = [*recent, now]
    try:
        answer = studio.mureka_call("POST", "/v1/lyrics/generate", {"prompt": prompt.strip()[:500]})
    except RuntimeError as error:
        raise HTTPException(502, f"Не получилось сочинить текст: {error}") from error
    return {"title": answer.get("title", ""), "lyrics": answer.get("lyrics", "")}


@app.post("/api/studio/lyrics/extend")
def api_studio_lyrics_extend(request: Request, lyrics: str = Form(...)):
    """Дописать продолжение текста (Mureka lyrics/extend) -- для «Продлить песню»."""
    user = current_user(request)
    if studio.restyle_engine() != "mureka":
        raise HTTPException(503, "Сочинение текста временно недоступно")
    now = time.time()
    recent = [t for t in _lyrics_calls.get(user.id, []) if now - t < 86400]
    if len(recent) >= studio.LYRICS_PER_DAY and not user.unlimited:
        raise HTTPException(429, "На сегодня текстов достаточно — попробуйте завтра")
    _lyrics_calls[user.id] = [*recent, now]
    try:
        answer = studio.mureka_call("POST", "/v1/lyrics/extend", {"lyrics": lyrics.strip()[:3000]})
    except RuntimeError as error:
        raise HTTPException(502, f"Не получилось дописать текст: {error}") from error
    return {"lyrics": answer.get("lyrics", "")}


@app.post("/api/studio/{job_id}/extend")
def api_studio_extend(job_id: str, request: Request, file: str = Form(""), lyrics: str = Form("")):
    """Продлить готовую песню Студии: Mureka song/extend с конца версии."""
    user = current_user(request)
    parent = storage.job(job_id)
    if (not parent or parent.user_id != user.id
            or (parent.settings or {}).get("kind") != "studio" or parent.status != "done"):
        raise HTTPException(404, "Готовая работа не найдена")
    chosen = next((f for f in (parent.result or {}).get("files") or [] if f["name"] == file), None)
    if not chosen or not chosen.get("mid"):
        raise HTTPException(409, "Продлить можно песню, созданную в Студии за последний месяц")
    if time.time() - parent.created_at > 28 * 86400:
        raise HTTPException(409, "Mureka продлевает только песни не старше месяца")
    if not lyrics.strip():
        raise HTTPException(400, "Напишите или сочините текст продолжения")
    service = studio.SERVICES["extend"]
    user = storage.user(user.id)
    if not user.unlimited and user.studio_credits < studio.credit_cost("extend") \
            and user.balance < service.price:
        raise HTTPException(402, f"{service.title} стоит {studio.credit_cost('extend')} кредитов или "
                                 f"{service.price:.0f} ₽ — пополните баланс на странице тарифов.")
    job = storage.create_job(user.id, parent.filename, {
        "kind": "studio", "mode": "extend", "title": service.title, "from": job_id,
        "variant": chosen.get("label", ""), "charged": 0, "engine": "mureka"})
    folder = studio_runner.folder(job.id)
    os.makedirs(folder, exist_ok=True)
    _studio_charge_and_submit(request, user, job.id, service, folder, {
        "mode": "extend", "song_id": chosen["mid"], "extend_at": chosen.get("ms") or 0,
        "lyrics": lyrics.strip()[:3000]})
    return {"jobId": job.id}


@app.post("/api/studio/voice")
async def api_studio_voice(request: Request, file: UploadFile = File(...), name: str = Form("Мой голос"),
                           consent: bool = Form(False)):
    """Запомнить голос для песен (Mureka song/vocal-clone). Нужна отметка,
    что голос свой или есть согласие его владельца."""
    user = current_user(request)
    if studio.restyle_engine() != "mureka" or not studio.voice_clone_open():
        raise HTTPException(503, "Свой голос скоро появится — ждём, пока Mureka откроет клонирование")
    if not consent:
        raise HTTPException(400, "Подтвердите, что это ваш голос или у вас есть согласие его владельца")
    if len(storage.voices(user.id)) >= 5:
        raise HTTPException(409, "Голосов уже пять — удалите ненужный")
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in ALLOWED or suffix in (".mid", ".midi"):
        raise HTTPException(400, "Нужна запись голоса: mp3, wav, m4a, ogg, flac")
    service = studio.SERVICES["voice"]
    if not user.unlimited and user.studio_credits < studio.credit_cost("voice") \
            and user.balance < service.price:
        raise HTTPException(402, f"{service.title} стоит {studio.credit_cost('voice')} кредитов или "
                                 f"{service.price:.0f} ₽ — пополните баланс на странице тарифов.")
    job = storage.create_job(user.id, name.strip()[:40] or "Мой голос", {
        "kind": "studio", "mode": "voice", "title": service.title, "charged": 0, "engine": "mureka",
        "voiceName": name.strip()[:40] or "Мой голос",
        "consent": {"at": time.time(), "ip": request.client.host if request.client else "",
                    "text": "мой голос или есть согласие владельца"}})
    folder = studio_runner.folder(job.id)
    os.makedirs(folder, exist_ok=True)
    size, limit = 0, 30 * 1024 * 1024
    with open(os.path.join(folder, f"source{suffix}"), "wb") as out:
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            if size > limit:
                out.close()
                shutil.rmtree(folder, ignore_errors=True)
                storage.update_job(job.id, status="error", error="Файл слишком большой")
                raise HTTPException(413, "Запись голоса — до 30 МБ")
            out.write(chunk)
    _studio_charge_and_submit(request, user, job.id, service, folder, {"mode": "voice"})
    return {"jobId": job.id}


@app.delete("/api/studio/voice/{voice_id}")
def api_studio_voice_delete(voice_id: str, request: Request):
    if not storage.delete_voice(current_user(request).id, voice_id):
        raise HTTPException(404, "Голос не найден")
    return {"ok": True}


@app.post("/api/studio/upload/{job_id}")
async def api_studio_upload(job_id: str, request: Request, e: int = 0, s: str = ""):
    """Результат от воркера -- по подписанной ссылке, по файлу за запрос."""
    _studio_link_job(job_id, "upload", e, s)
    name = studio.safe_file_name(request.headers.get("x-file-name", ""))
    if not name:
        raise HTTPException(400, "Недопустимое имя файла")
    target = os.path.join(studio_runner.folder(job_id), name)
    # 12 дорожек WAV глубокого разделения -- сотни мегабайт одним архивом
    size, limit = 0, 1024 * 1024 * 1024
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
    if safe == "cover.jpg":
        return FileResponse(path, media_type="image/jpeg",
                            headers={"Cache-Control": "private, max-age=86400"})
    if safe.endswith((".mid", ".zip")):
        base = Path(job.filename).stem[:60] or "naslux"
        return FileResponse(path, filename=f"{base} — {safe}",
                            media_type="audio/midi" if safe.endswith(".mid") else "application/zip")
    extra = next((x for x in (job.result or {}).get("extras") or [] if x["name"] == safe), None)
    label = extra["label"] if extra else studio.label_of((job.settings or {}).get("mode", ""), safe)
    return FileResponse(path, filename=download_name(job.filename, label, ".mp3"))


EXTRAS_PER_TRACK = 8
# Диктофоны телефонов пишут и в aac/opus/amr -- ffmpeg прочтёт всё
EXTRA_ALLOWED = (".wav", ".mp3", ".flac", ".ogg", ".m4a", ".aiff", ".aif",
                 ".aac", ".opus", ".webm", ".amr", ".3gp", ".wma", ".mp4")


@app.post("/api/studio/{job_id}/extra")
async def api_studio_extra(job_id: str, request: Request, file: UploadFile = File(...),
                           label: str = Form(""), version: str = Form("")):
    """Своя дорожка мультитрека (запись с репетиции, подложка) -- хранится
    при треке, как и его партии: открыл на репетиции -- она уже там.
    Перекодируем в mp3: меньше трафика, и любой браузер её прочитает."""
    user = current_user(request)
    job = storage.job(job_id)
    if (not job or job.user_id != user.id or (job.settings or {}).get("kind") != "studio"
            or job.status != "done"):
        raise HTTPException(404, "Готовая работа не найдена")
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in EXTRA_ALLOWED:
        raise HTTPException(400, "Нужен аудиофайл: mp3, wav, m4a, flac, ogg, aac, opus")
    if len((job.result or {}).get("extras") or []) >= EXTRAS_PER_TRACK:
        raise HTTPException(400, f"К одному треку — не больше {EXTRAS_PER_TRACK} своих дорожек")
    folder = studio_runner.folder(job_id)
    os.makedirs(folder, exist_ok=True)
    name = f"extra_{secrets.token_hex(4)}.mp3"
    raw = os.path.join(folder, f"{name}.upload{suffix}")
    size, limit = 0, MAX_UPLOAD_MB * 1024 * 1024
    try:
        with open(raw, "wb") as out:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > limit:
                    raise HTTPException(413, f"Файл больше {MAX_UPLOAD_MB} МБ")
                out.write(chunk)
        done = await asyncio.to_thread(subprocess.run, [
            "ffmpeg", "-v", "error", "-y", "-i", raw, "-vn", "-ac", "2", "-ar", "44100", "-b:a", "192k",
            os.path.join(folder, name)], capture_output=True, timeout=300)
        if done.returncode or not os.path.isfile(os.path.join(folder, name)):
            raise HTTPException(400, "Этот файл не получилось прочитать — попробуйте mp3 или wav")
    finally:
        if os.path.exists(raw):
            os.remove(raw)
    entry = {"name": name, "label": (label.strip() or "Своя дорожка")[:40], "for": version[:120]}
    with studio_runner._analysis_lock:
        fresh = storage.job(job_id)
        result = dict(fresh.result or {})
        result["extras"] = (result.get("extras") or []) + [entry]
        storage.update_job(job_id, result=result)
    return {**entry, "url": f"/api/studio/file/{job_id}/{name}"}


@app.delete("/api/studio/{job_id}/extra/{name}")
def api_studio_extra_delete(job_id: str, name: str, request: Request):
    user = current_user(request)
    job = storage.job(job_id)
    if not job or job.user_id != user.id:
        raise HTTPException(404, "Готовая работа не найдена")
    with studio_runner._analysis_lock:
        fresh = storage.job(job_id)
        result = dict(fresh.result or {})
        extras = result.get("extras") or []
        if not any(x["name"] == name for x in extras):
            raise HTTPException(404, "Дорожка не найдена")
        result["extras"] = [x for x in extras if x["name"] != name]
        storage.update_job(job_id, result=result)
    path = os.path.join(studio_runner.folder(job_id), name)
    if os.path.isfile(path):
        os.remove(path)
    return {"ok": True}


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

def safe_next(next_path: str) -> str:
    """Куда вернуть человека после оплаты: только свой путь на этом сайте
    (/studio, /pricing...), без чужих доменов и протоколов."""
    return next_path if re.fullmatch(r"/[A-Za-z0-9/_-]{0,60}", next_path or "") else "/"


def _start_payment(request: Request, user, amount: float, title: str, plan: str,
                   next_path: str = "/") -> dict:
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
            user.id, amount, f"{base}{safe_next(next_path)}?paid=1",
            title=title,
            notify_url=f"{base}/api/webhook/{gateway.name}",
            email=user.email or "",
            fail_url=f"{base}{safe_next(next_path)}?paid=0",
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


PLAN_TITLES = {"single": "один трек", "month": "«Музыкант» на месяц", "topup": "пополнение баланса",
               "studio_month": "«Студия» на месяц", "pro_month": "«Про» на месяц",
               "studio10": "100 кредитов Студии", "studio30": "300 кредитов Студии"}
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
def api_subscribe(request: Request, plan: str = Form("month"), next: str = Form("/")):  # noqa: A002
    """Создать платёж: подписка на месяц или один трек."""
    if plan not in billing.PLANS:
        raise HTTPException(400, "Неизвестный тариф")
    user = current_user(request)
    spec = billing.PLANS[plan]
    return _start_payment(request, user, spec["price"], spec["title"], plan, next)


@app.post("/api/topup")
def api_topup(request: Request, amount: float = Form(...), next: str = Form("/")):  # noqa: A002
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
        request, user, amount, f"Пополнение баланса на {amount:.0f} ₽", "topup", next
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

    # Провайдер выбирается по адресу, а не по текущей переменной
    # PAYMENT_PROVIDER: URL прописан в кабинете мерчанта именно затем,
    # чтобы смена активного провайдера не роняла уведомления от старого
    # (см. докстринг выше). billing.provider() читает именно эту
    # переменную и годится только для СОЗДАНИЯ платежа -- для проверки
    # входящего уведомления нужен ровно тот сервис, что в пути запроса.
    gateway_cls = billing.PROVIDERS.get(gateway_name)
    if gateway_cls is None:
        raise HTTPException(404, "Неизвестный платёжный сервис")
    gateway = gateway_cls()
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
        try:
            billing.apply_plan(
                storage, record["user_id"], record.get("plan") or "month", record.get("amount")
            )
        except Exception as error:                   # noqa: BLE001
            # mark_paid_once уже закоммитил status='succeeded' отдельной
            # транзакцией -- если начисление здесь упадёт (блокировка
            # SQLite, перезапуск сервиса между этими двумя шагами), платёж
            # навсегда остался бы "succeeded", а mark_paid_once на
            # повторном уведомлении от сервиса увидел бы его уже оплаченным
            # и молча пропустил начисление. Откатываем статус, чтобы
            # ближайший повтор уведомления (платёжные сервисы их шлют)
            # попробовал начислить ещё раз, а не ушёл "succeeded (повтор,
            # начислять нечего)".
            storage.set_payment_status(record["id"], "pending")
            storage.save_notice(
                gateway_name, payload, False,
                f"оплата зачтена, но начисление упало: {error!r}",
            )
            raise HTTPException(
                502, "Платёж принят, но начисление не удалось — повторите уведомление"
            ) from error
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
    # Настоящий ICO: робот Яндекса не принимает SVG под именем favicon.ico
    return FileResponse(STATIC_DIR / "favicon.ico", media_type="image/x-icon")


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
