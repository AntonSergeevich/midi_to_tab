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
import shutil
import sys
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

from . import auth, billing, mailer, support
from .jobs import JobRunner
from .storage import Storage

DATA_DIR = os.environ.get("MIDI2TAB_DATA", "data")
SECRET = os.environ.get("MIDI2TAB_SECRET", "")
MAX_UPLOAD_MB = int(os.environ.get("MIDI2TAB_MAX_MB", "60"))
# Ключ владельца. Первый, кто войдёт с ним, получает права администратора
# и безлимит. Без ключа админка недоступна вообще -- это безопаснее, чем
# пароль по умолчанию, который забывают сменить.
ADMIN_KEY = os.environ.get("MIDI2TAB_ADMIN_KEY", "")
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


storage = Storage(os.path.join(DATA_DIR, "app.db"))
runner = JobRunner(storage, DATA_DIR)
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
    return HTMLResponse((STATIC_DIR / "index.html").read_text(encoding="utf-8"))


def require_admin(request: Request):
    """Пускать в админку только помеченных администраторами."""
    user = current_user(request)
    if not user.is_admin:
        raise HTTPException(403, "Нужны права администратора")
    return user


@app.get("/admin", response_class=HTMLResponse)
def admin_page() -> HTMLResponse:
    return HTMLResponse((STATIC_DIR / "admin.html").read_text(encoding="utf-8"))


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


@app.get("/api/admin/users")
def api_admin_users(request: Request):
    require_admin(request)
    users = storage.all_users()
    return {
        "stats": storage.stats(),
        "users": [
            {
                "id": u.id,
                "short": u.id[:8],
                "at": u.created_at,
                "freeUsed": u.free_used,
                "paidUntil": u.paid_until,
                "subscribed": u.subscribed,
                "unlimited": u.unlimited,
                "isAdmin": u.is_admin,
                "note": u.note,
                "tracks": len(storage.root_jobs(u.id, 500)),
            }
            for u in users
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
    return HTMLResponse((STATIC_DIR / "account.html").read_text(encoding="utf-8"))


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


@app.get("/privacy", response_class=HTMLResponse)
def privacy_page() -> HTMLResponse:
    return HTMLResponse((STATIC_DIR / "privacy.html").read_text(encoding="utf-8"))


@app.get("/offer", response_class=HTMLResponse)
def offer_page() -> HTMLResponse:
    return HTMLResponse((STATIC_DIR / "offer.html").read_text(encoding="utf-8"))


@app.get("/library", response_class=HTMLResponse)
def library_page() -> HTMLResponse:
    return HTMLResponse((STATIC_DIR / "library.html").read_text(encoding="utf-8"))


@app.get("/player/{job_id}", response_class=HTMLResponse)
def player_page(job_id: str) -> HTMLResponse:
    if not storage.job(job_id):
        raise HTTPException(404, "Задание не найдено")
    return HTMLResponse((STATIC_DIR / "player.html").read_text(encoding="utf-8"))


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
    payload["lyricsReady"] = lyrics_mod.available()[0]
    payload["lyricsModels"] = list(lyrics_mod.MODELS)
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
    billing.consume(storage, user)
    storage.update_job(job.id, counted=True)
    runner.submit_analysis(job.id, target)

    response = JSONResponse({"jobId": job.id})
    attach_cookie(response, user.id)
    return response


@app.get("/api/job/{job_id}")
def api_job(job_id: str):
    job = storage.job(job_id)
    if not job:
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
def api_make_lyrics(job_id: str, request: Request, model: str = Form(lyrics_mod.DEFAULT_MODEL)):
    """Распознать текст песни по вокальной партии."""
    job = storage.job(job_id)
    if not job or job.status != "done" or not job.result:
        raise HTTPException(404, "Разбор ещё не готов")
    ok, why = lyrics_mod.available()
    if not ok:
        raise HTTPException(503, why)
    runner.submit_lyrics(job_id, model)
    return {"ok": True}


@app.post("/api/job/{job_id}/tabs/{stem_key}")
def api_make_tabs(job_id: str, stem_key: str, request: Request):
    """Создать MIDI и табы для выбранной партии."""
    parent = storage.job(job_id)
    if not parent or parent.status != "done" or not parent.result:
        raise HTTPException(404, "Разбор ещё не готов")
    if stem_key not in (parent.result.get("paths") or {}).get("parts", {}):
        raise HTTPException(404, "Такой партии нет")

    user = current_user(request)
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
def api_part_file(job_id: str, stem_key: str):
    """Аудио одной партии -- его слушают в плеере."""
    job = storage.job(job_id)
    if not job or not job.result:
        raise HTTPException(404, "Файл не готов")
    path = (job.result.get("paths") or {}).get("parts", {}).get(stem_key)
    if not path or not os.path.isfile(path):
        raise HTTPException(404, "Партия не найдена")
    labels = {p["key"]: p["label"] for p in (job.result.get("parts") or [])}
    return FileResponse(path, filename=download_name(job.filename, labels.get(stem_key, stem_key),
                                                     Path(path).suffix))


@app.get("/api/file/{job_id}/{kind}")
def api_file(job_id: str, kind: str):
    job = storage.job(job_id)
    if not job or not job.result:
        raise HTTPException(404, "Файл не готов")
    path = (job.result.get("paths") or {}).get(kind)
    if not path or not os.path.isfile(path):
        raise HTTPException(404, "Файл не найден")
    parent = storage.job((job.settings or {}).get("parent", "")) if job.settings else None
    title = (parent or job).filename
    stem = (job.settings or {}).get("stem", "")
    return FileResponse(path, filename=download_name(title, stem, Path(path).suffix))


# ------------------------------------------------------------------ оплата

@app.post("/api/subscribe")
def api_subscribe(request: Request, plan: str = Form("month")):
    """Создать платёж: подписка на месяц или один трек."""
    if plan not in billing.PLANS:
        raise HTTPException(400, "Неизвестный тариф")
    user = current_user(request)
    gateway = billing.provider()
    if not gateway.configured():
        raise HTTPException(
            503,
            "Приём оплаты пока не подключён. Нужны реквизиты магазина "
            "в переменных окружения, см. web/README.md.",
        )
    spec = billing.PLANS[plan]
    base = str(request.base_url).rstrip("/")
    created = gateway.create_payment(user.id, spec["price"], f"{base}/?paid=1")
    storage.create_payment(user.id, spec["price"], created.get("id"), plan=plan)
    url = (created.get("confirmation") or {}).get("confirmation_url")
    return {"paymentUrl": url, "paymentId": created.get("id"), "plan": plan}


@app.post("/api/webhook/{gateway_name}")
async def api_webhook(gateway_name: str, request: Request):
    """
    Уведомление об оплате.

    Адрес включает имя сервиса, потому что его прописывают в кабинете
    мерчанта и менять там что-то задним числом неудобно: пусть у каждого
    будет свой, а смена провайдера не требует править настройки у старого.
    Форма тела бывает и JSON, и обычной формой -- принимаем обе.
    """
    try:
        payload = await request.json()
    except Exception:
        payload = dict(await request.form())
    gateway = billing.provider()
    verified = gateway.verify_webhook(payload)
    if not verified:
        raise HTTPException(400, "Неожиданный формат уведомления")
    provider_id, status = verified
    record = storage.payment_by_provider(provider_id)
    if not record:
        raise HTTPException(404, "Платёж не найден")
    storage.set_payment_status(record["id"], status)
    if status == "succeeded":
        billing.apply_plan(storage, record["user_id"], record.get("plan") or "month")
    return {"ok": True}


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    """Браузер запрашивает иконку сам; без неё в консоли висит 404."""
    return FileResponse(STATIC_DIR / "favicon.svg", media_type="image/svg+xml")


@app.get("/api/health")
def api_health():
    return {"ok": True, "separation": separate.available()[0],
            "recognition": audioin.available()[0]}


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
