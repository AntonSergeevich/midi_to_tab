"""
Студия: обработка трека нейросетями на GPU-воркере в RunPod.

Три услуги:
  restyle -- переделать трек в другой стиль (ACE-Step cover), с текстом
             песни и «силой переделки»;
  enrich  -- дописать партию поверх трека (ACE-Step lego): барабаны, бас...;
  stems   -- разделить на 6 партий (Demucs htdemucs_6s на видеокарте).

Своей видеокарты у сервера нет, поэтому работу делает воркер на RunPod
(worker/ в корне репозитория). Воркер не знает никаких секретов сайта:
исходник он скачивает, а результат отдаёт по одноразовым подписанным
ссылкам, которые живут, пока идёт задача. Деньги списываются с баланса
при постановке задачи и возвращаются, если задача не удалась.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from .storage import Storage

RUNPOD_API = "https://api.runpod.ai/v2"
RUNPOD_REST = "https://rest.runpod.io/v1"
USER_AGENT = "naslux-studio/1.0 (+https://naslux.ru)"
WORKER_NAME = "naslux-worker"
POLL_SECONDS = 10
JOB_TIMEOUT = 45 * 60
LINK_TTL = 2 * 3600
MAX_SECONDS = 600


@dataclass(frozen=True)
class Service:
    title: str
    price: float


# Цены посчитаны от живых списаний Mureka 26.09 (курс ~100 ₽/$ с учётом
# комиссий): песня с нуля (mureka-9, 2 версии) -- $0.09 ≈ 9 ₽; переделка
# (remix, 2 версии) -- $0.40 ≈ 40 ₽; текст -- доли цента. Разделение на
# партии у Mureka -- $0.70 и всего 3 партии, поэтому оно остаётся на
# нашем Demucs (6 партий, ~0.5 ₽).
SERVICES = {
    "create": Service("Песня с нуля", 49.0),
    "restyle": Service("Переделка в другой стиль", 99.0),
    "enrich": Service("Дописать партию", 39.0),
    "stems": Service("Разделение на партии", 19.0),
}

# Переделка в стиль на ACE-Step не дотягивает до качества, за которое
# можно брать деньги (живые пробы владельца: то каша, то почти один в один
# с оригиналом). Переделку делает Mureka (официальный API): на «Земле в
# иллюминаторе» она сменила инструменты, сохранив мелодию и гармонию, --
# а Suno ту же песню не пропустил вовсе. Без ключа Mureka переделка
# открыта только безлимитным -- на старом движке, для проб.
RESTYLE_OPEN = False
MUREKA_API = "https://api.mureka.ai"
MUREKA_UPLOAD_LIMIT = 10 * 1024 * 1024
MUREKA_MAX_SECONDS = 350       # remix принимает 10..350 с
MUREKA_STATES = {"preparing": "Готовим трек", "queued": "В очереди у Mureka",
                 "running": "Нейросеть переделывает трек", "streaming": "Дописываем версии"}

# Сколько генераций из пакета (billing.PLANS studio10/30) стоит услуга.
# Генерация в пакете -- 33-39 ₽; переделка стоит нам ~40 ₽, поэтому
# списывает три. Разделение дешёвое и идёт только с баланса.
PACK_COST = {"create": 1, "restyle": 2, "enrich": 1}
PACK_MODES = tuple(PACK_COST)
MUREKA_SONG_MODEL = "mureka-9"
KEEP_VARIANTS = 2  # аранжировок под голос оригинала за одну переделку
# Голос: у song/generate есть параметр gender (male/female); дуэта в нём нет,
# его, как и голос для переделки (у remix параметра нет), просим в описании.
VOICES = {"": ("Любой", ""), "male": ("Мужской", "male vocals"),
          "female": ("Женский", "female vocals"),
          "duet": ("Дуэт", "duet, male and female vocals")}   # $0.045 за версию; auto может взять дорогую 9.5
LYRICS_PER_DAY = 30              # бесплатное сочинение текста -- с ограничением

# Готовые стили -- подсказки для нейросети на английском: так её учили.
PRESETS = {
    "numetal": ("Nu-metal", "nu metal, heavy downtuned 7-string guitars, aggressive drums, "
                "distorted bass, powerful male vocals, Linkin Park, Korn style"),
    "rock": ("Рок", "alternative rock, overdriven electric guitars, live drums, bass, "
             "energetic vocals"),
    "metalcore": ("Металкор", "metalcore, breakdowns, double bass drums, heavy riffs, "
                  "screamed and clean vocals"),
    "poppunk": ("Поп-панк", "pop punk, fast drums, bright distorted guitars, catchy vocals"),
    "acoustic": ("Акустика", "acoustic guitar, soft percussion, warm intimate vocals, unplugged"),
    "synthwave": ("Синтвейв", "synthwave, 80s analog synths, gated reverb drums, retro"),
    "lofi": ("Lo-fi", "lo-fi hip hop, dusty drums, mellow keys, vinyl crackle, chill"),
    "orchestral": ("Оркестр", "epic orchestral, strings, brass, choir, cinematic drums"),
}

TRACKS = {
    "drums": "Барабаны", "bass": "Бас", "guitar": "Гитара", "keyboard": "Клавиши",
    "strings": "Струнные", "synth": "Синтезатор", "percussion": "Перкуссия",
    "brass": "Духовые", "backing_vocals": "Бэк-вокал",
}

STEM_LABELS = {
    "vocals": "Вокал", "guitar": "Гитара", "bass": "Бас", "drums": "Барабаны",
    "piano": "Клавиши", "other": "Остальное",
}

STATES = {
    "IN_QUEUE": "Ждём свободную видеокарту",
    "IN_PROGRESS": "Нейросеть работает",
}


def call(method: str, url: str, body: dict | None = None, timeout: int = 60) -> dict:
    """Запрос к RunPod. Только стандартная библиотека: на сервере сайта
    нет лишних пакетов, и ради пары запросов их ставить не стоит."""
    data = json.dumps(body).encode() if body is not None else None
    # Свой User-Agent обязателен: перед API RunPod стоит Cloudflare, и
    # стандартный «Python-urllib/3.x» он отбивает с 403 «error code: 1010»
    # (так и сломалось на первом живом запуске с сайта).
    request = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {api_key()}", "Content-Type": "application/json",
        "User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"RunPod ответил {error.code}: "
                           f"{short_error(error.read()[:2000].decode(errors='replace'))}") from error
    return json.loads(raw or b"{}")


def api_key() -> str:
    return os.environ.get("RUNPOD_API_KEY", "")


def mureka_key() -> str:
    return os.environ.get("MUREKA_API_KEY", "")


def restyle_engine() -> str:
    """Переделка и песни с нуля -- Mureka, если на сервере есть её ключ.

    NASLUX_RESTYLE_ENGINE=runpod возвращает переделку на ACE-Step (для
    проб). С российского сервера Mureka напрямую не отвечает (403 «Service
    unavailable»), поэтому запросы к ней идут через ретранслятор на RunPod
    (relay/) -- см. use_relay()."""
    wanted = os.environ.get("NASLUX_RESTYLE_ENGINE", "mureka").strip().lower()
    return "mureka" if wanted == "mureka" and mureka_key() else "runpod"


def use_relay() -> bool:
    """Ходить к Mureka через ретранслятор (по умолчанию -- если есть RunPod)."""
    return bool(api_key()) and os.environ.get("NASLUX_MUREKA_DIRECT", "") != "1"


RELAY_NAME = "naslux-relay"
_relay_cache: dict[str, str] = {}


def relay_endpoint_id() -> str:
    configured = os.environ.get("NASLUX_RELAY_ENDPOINT", "")
    if configured:
        return configured
    if "id" not in _relay_cache:
        found = [e for e in call("GET", f"{RUNPOD_REST}/endpoints")
                 if e.get("name", "").startswith(RELAY_NAME)]
        if not found:
            raise RuntimeError(f"На RunPod нет ретранслятора {RELAY_NAME}")
        _relay_cache["id"] = found[0]["id"]
    return _relay_cache["id"]


COVER_NAME = "naslux-cover"
_cover_cache: dict[str, str] = {}


def cover_endpoint_id() -> str:
    configured = os.environ.get("NASLUX_COVER_ENDPOINT", "")
    if configured:
        return configured
    if "id" not in _cover_cache:
        found = [e for e in call("GET", f"{RUNPOD_REST}/endpoints")
                 if e.get("name", "").startswith(COVER_NAME)]
        if not found:
            raise RuntimeError(f"На RunPod нет эндпоинта {COVER_NAME}")
        _cover_cache["id"] = found[0]["id"]
    return _cover_cache["id"]


def make_cover(task_input: dict, title: str, lyrics: str, style: str, seed: int) -> bool:
    """Обложка к песне (cover/handler.py на RunPod): cover.jpg приходит в папку
    задачи по ссылке загрузки. Обложка -- украшение: любая ошибка -> False,
    песня от этого не страдает."""
    if not api_key() or not task_input.get("upload_url") \
            or os.environ.get("NASLUX_COVERS", "1") == "0":
        return False
    try:
        endpoint = cover_endpoint_id()
        job = call("POST", f"{RUNPOD_API}/{endpoint}/runsync", {"input": {
            "title": title[:120], "lyrics": lyrics[:1500], "style": style[:200], "seed": seed,
            "upload_url": task_input["upload_url"]}}, timeout=180)
        started = time.time()
        while job.get("status") in ("IN_QUEUE", "IN_PROGRESS") and time.time() - started < 600:
            time.sleep(5)
            job = call("GET", f"{RUNPOD_API}/{endpoint}/status/{job['id']}")
        return bool((job.get("output") or {}).get("ok"))
    except Exception as error:  # noqa: BLE001
        print(f"обложка не получилась: {error}", file=sys.stderr, flush=True)
        _cover_cache.pop("id", None)
        return False


def relay_run(task: dict, timeout: int = 900) -> dict:
    """Операция ретранслятора: runsync, а если не успел -- опрос статуса."""
    endpoint = relay_endpoint_id()
    started = time.time()
    try:
        job = call("POST", f"{RUNPOD_API}/{endpoint}/runsync", {"input": task}, timeout=180)
    except RuntimeError as error:
        # Ретранслятор пересоздали -- у него новый id: забыть старый и найти заново.
        if "ответил 404" not in str(error) or "id" not in _relay_cache:
            raise
        _relay_cache.pop("id", None)
        endpoint = relay_endpoint_id()
        job = call("POST", f"{RUNPOD_API}/{endpoint}/runsync", {"input": task}, timeout=180)
    while job.get("status") in ("IN_QUEUE", "IN_PROGRESS"):
        if time.time() - started > timeout:
            raise RuntimeError("ретранслятор не ответил вовремя")
        time.sleep(3)
        job = call("GET", f"{RUNPOD_API}/{endpoint}/status/{job['id']}")
    if job.get("status") != "COMPLETED":
        raise RuntimeError(f"ретранслятор: {job.get('status')} {str(job.get('error'))[:200]}")
    return job.get("output") or {}


def restyle_open() -> bool:
    return restyle_engine() == "mureka" or RESTYLE_OPEN


def short_error(detail: str) -> str:
    """Ответ сервиса -- в короткую строку: вместо HTML-страницы её заголовок."""
    if "<html" in detail.lower() or "<!doctype" in detail.lower():
        title = re.search(r"<title>(.*?)</title>", detail, re.I | re.S)
        return f"страница «{title.group(1).strip()}»" if title else "HTML-страница вместо ответа"
    return detail[:300]


def available() -> tuple[bool, str]:
    if not api_key() and not mureka_key():
        return False, "Студия ещё не подключена: нет ключа RunPod на сервере"
    return True, ""


MUREKA_BUSY_WAIT = int(os.environ.get("MUREKA_BUSY_WAIT", 20 * 60))  # ждать очереди, с
MUREKA_NOT_BUSY = ("balance", "quota", "credit", "insufficient", "recharge", "payment",
                   "余额", "额度")


def mureka_call(method: str, path: str, body: dict | None = None, timeout: int = 60) -> dict:
    """Запрос к Mureka -- напрямую или через ретранслятор. 429 -- занят лимит
    одновременных запросов (на тарифе Trial он один): второй клиент не
    получает ошибку, а ждёт своей очереди; 429 про деньги -- сразу ошибка."""
    deadline = time.time() + MUREKA_BUSY_WAIT
    while True:
        if use_relay():
            out = relay_run({"op": "call", "method": method, "path": path, "body": body})
            if out.get("ok"):
                return out.get("json") or {}
            code, detail = out.get("status") or 0, out.get("error") or ""
        else:
            code, detail, answer = _mureka_direct(method, path, body, timeout)
            if answer is not None:
                return answer
        money = any(word in detail.lower() for word in MUREKA_NOT_BUSY)
        if code == 429 and not money and time.time() < deadline:
            print(f"[mureka] {path}: 429, ждём очереди -- {detail[:150]}",
                  file=sys.stderr, flush=True)
            time.sleep(POLL_SECONDS)
            continue
        raise RuntimeError(f"Mureka ответила {code}: {short_error(detail)}")


def _mureka_direct(method, path, body, timeout):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(f"{MUREKA_API}{path}", data=data, method=method, headers={
        "Authorization": f"Bearer {mureka_key()}", "Content-Type": "application/json",
        "User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return getattr(response, "status", 200), "", json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as error:
        return error.code, error.read()[:2000].decode(errors="replace"), None


def mureka_upload_for(task_input: dict, path: str, purpose: str) -> str:
    """Загрузить файл задачи в Mureka: через ретранслятор -- по подписанной
    ссылке сайта (task_input["mureka_url"]), напрямую -- с диска."""
    if not use_relay():
        return mureka_upload(path, purpose)
    out = relay_run({"op": "upload", "source_url": task_input["mureka_url"],
                     "purpose": purpose, "filename": "track.mp3"})
    if not out.get("ok") or not (out.get("json") or {}).get("id"):
        raise RuntimeError(f"Mureka не приняла файл ({out.get('status')}): "
                           f"{short_error(out.get('error') or str(out.get('json')))}")
    return out["json"]["id"]


def fetch_result(task_input: dict, url: str, folder: str, name: str) -> None:
    """Забрать файл Mureka в папку задачи: через ретранслятор (он отдаёт его
    на подписанный адрес загрузки сайта) или напрямую."""
    if not use_relay():
        download(url, os.path.join(folder, name))
        return
    out = relay_run({"op": "fetch", "url": url, "upload_url": task_input["upload_url"],
                     "name": name})
    if not out.get("ok"):
        raise RuntimeError(f"не забрать {name}: {short_error(out.get('error') or '')}")


def mureka_upload(path: str, purpose: str) -> str:
    """POST /v1/files/upload -- multipart, поле file и purpose; возвращает id."""
    boundary = uuid.uuid4().hex
    with open(path, "rb") as f:
        content = f.read()
    name = os.path.basename(path)
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"purpose\"\r\n\r\n"
            f"{purpose}\r\n--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
            f"filename=\"{name}\"\r\nContent-Type: audio/mpeg\r\n\r\n").encode() \
        + content + f"\r\n--{boundary}--\r\n".encode()
    request = urllib.request.Request(f"{MUREKA_API}/v1/files/upload", data=body, method="POST",
                                     headers={"Authorization": f"Bearer {mureka_key()}",
                                              "Content-Type": f"multipart/form-data; boundary={boundary}",
                                              "User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            uploaded = json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"Mureka не приняла файл ({error.code}): "
                           f"{short_error(error.read()[:2000].decode(errors='replace'))}") from error
    if not uploaded.get("id"):
        raise RuntimeError(f"Mureka не вернула id файла: {str(uploaded)[:200]}")
    return uploaded["id"]


def mureka_source(source: str, folder: str) -> str:
    """Трек под условия remix: mp3/m4a, до 10 МБ и до 350 секунд.

    ffmpeg на сервере есть (deploy/install.sh): режем и кодируем в mp3
    192 кбит/с -- 350 секунд выходят в ~8.4 МБ. Без ffmpeg годится только
    исходник, который и так подходит."""
    target = os.path.join(folder, "for_mureka.mp3")
    if shutil.which("ffmpeg"):
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", source, "-t", str(MUREKA_MAX_SECONDS),
                        "-ac", "2", "-b:a", "192k", target], check=True, timeout=300)
        return target
    if source.lower().endswith((".mp3", ".m4a")) and os.path.getsize(source) <= MUREKA_UPLOAD_LIMIT:
        return source
    raise RuntimeError("трек нужно перекодировать в mp3 до 10 МБ, а ffmpeg на сервере нет")


def mureka_audio(source: str, folder: str) -> str:
    """Вокал для track/generate (purpose audio: mp3 до 10 МБ): моно 128 кбит/с --
    это ~5.8 МБ даже на 6 минутах."""
    target = os.path.join(folder, "for_mureka.mp3")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", source, "-t", str(MUREKA_MAX_SECONDS),
                    "-ac", "1", "-b:a", "128k", target], check=True, timeout=300)
    return target


def mix_vocals(vocals: str, backing: str, target: str) -> None:
    """Голос поверх новой аранжировки: без нормализации amix (иначе оба тише
    вдвое), длина -- по голосу."""
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", vocals, "-i", backing, "-filter_complex",
                    "[0:a][1:a]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.95",
                    "-b:a", "256k", target], check=True, timeout=300)


def download(url: str, target: str) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=300) as response, open(target, "wb") as out:
        shutil.copyfileobj(response, out)


_endpoint_cache: dict[str, str] = {}


def endpoint_id() -> str:
    """ID эндпоинта: из окружения или по имени через API RunPod (один раз)."""
    configured = os.environ.get("NASLUX_WORKER_ENDPOINT", "")
    if configured:
        return configured
    if "id" not in _endpoint_cache:
        found = [e for e in call("GET", f"{RUNPOD_REST}/endpoints")
                 if e.get("name", "").startswith(WORKER_NAME)]
        if not found:
            raise RuntimeError(f"На RunPod нет эндпоинта {WORKER_NAME}")
        _endpoint_cache["id"] = found[0]["id"]
    return _endpoint_cache["id"]


# ----------------------------------------------------------- подписи ссылок

def sign(secret: str, job_id: str, purpose: str, expires: int) -> str:
    message = f"{job_id}:{purpose}:{expires}".encode()
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def link(base: str, secret: str, job_id: str, purpose: str) -> str:
    expires = int(time.time()) + LINK_TTL
    return (f"{base}/api/studio/{purpose}/{job_id}"
            f"?e={expires}&s={sign(secret, job_id, purpose, expires)}")


def check_link(secret: str, job_id: str, purpose: str, expires: int, signature: str) -> bool:
    if expires < time.time():
        return False
    return hmac.compare_digest(sign(secret, job_id, purpose, expires), signature or "")


def safe_file_name(name: str) -> str | None:
    """Имя файла от воркера: только простое имя mp3 (или обложка), без путей."""
    if name == "cover.jpg":
        return name
    return name if re.fullmatch(r"[A-Za-z0-9_.-]{1,80}\.mp3", name or "") else None


def label_of(mode: str, name: str) -> str:
    stem = name.rsplit(".", 1)[0]
    if mode == "stems":
        return STEM_LABELS.get(stem, stem)
    if mode == "enrich":
        return "С дописанной партией"
    number = stem.rsplit("_", 1)[-1]  # restyle_2 / create_2 / backing_2
    if stem.startswith("backing_"):
        return f"Минус {number} — без голоса"
    return f"Вариант {number}" if number.isdigit() else "Новая версия"


# ------------------------------------------------------------------ задачи

class StudioRunner:
    """Ставит задачи на RunPod и следит за ними в фоне."""

    def __init__(self, storage: Storage, data_dir: str) -> None:
        self.storage = storage
        self.data_dir = data_dir
        self.pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="studio")

    def folder(self, job_id: str) -> str:
        return os.path.join(self.data_dir, "studio", job_id)

    def submit(self, job_id: str, worker_input: dict) -> None:
        # Задание для воркера хранится в самой задаче: сервер
        # перезапускается при каждом автодеплое, и незаконченная задача
        # должна продолжиться после рестарта, а не пропасть вместе с
        # деньгами.
        job = self.storage.job(job_id)
        self.storage.update_job(job_id, settings={**(job.settings or {}), "input": worker_input})
        self.pool.submit(self._run, job_id)

    def resume(self) -> int:
        """После перезапуска -- снова следить за незаконченными задачами."""
        pending = self.storage.unfinished_studio_jobs()
        for job in pending:
            self.pool.submit(self._run, job.id)
        return len(pending)

    def shutdown(self) -> None:
        self.pool.shutdown(wait=False, cancel_futures=True)

    def _run(self, job_id: str) -> None:
        job = self.storage.job(job_id)
        if job and (job.settings or {}).get("engine") == "mureka":
            self._run_mureka(job_id)
            return
        try:
            job = self.storage.job(job_id)
            settings = dict(job.settings or {})
            remote = settings.get("remote")
            if not remote:
                endpoint = endpoint_id()
                self.storage.update_job(job_id, status="running",
                                        stage="Отправляем на видеокарту", progress=3)
                started = call("POST", f"{RUNPOD_API}/{endpoint}/run", {"input": settings["input"]})
                remote = {"endpoint": endpoint, "id": started["id"]}
                settings["remote"] = remote
                self.storage.update_job(job_id, settings=settings)
            endpoint, remote_id = remote["endpoint"], remote["id"]
            while True:
                status = call("GET", f"{RUNPOD_API}/{endpoint}/status/{remote_id}")
                state = status.get("status")
                if state in STATES:
                    elapsed = time.time() - job.created_at
                    # Честной доли готовности воркер не сообщает; растим
                    # полоску по времени, не доводя до конца.
                    self.storage.update_job(job_id, status="running", stage=STATES[state],
                                            progress=min(90, 5 + elapsed / 6))
                    if elapsed > JOB_TIMEOUT:
                        call("POST", f"{RUNPOD_API}/{endpoint}/cancel/{remote_id}", {})
                        raise RuntimeError("видеокарта не справилась за отведённое время")
                    time.sleep(POLL_SECONDS)
                    continue
                output = status.get("output") or {}
                if state != "COMPLETED" or not output.get("ok"):
                    raise RuntimeError(output.get("error") or status.get("error") or state)
                break
            self._finish(job_id, output, status)
        except Exception as error:  # noqa: BLE001 -- любая ошибка -> деньги назад
            self._fail(job_id, str(error))

    def _mureka_wait(self, job_id: str, kind: str, task_id: str, created_at: float,
                     stage: str = "") -> dict:
        """Опрос задачи Mureka до конца; ход -- в задачу сайта."""
        while True:
            status = mureka_call("GET", f"/v1/{kind}/query/{task_id}")
            state = status.get("status")
            if state not in MUREKA_STATES:
                if state != "succeeded":
                    raise RuntimeError(status.get("failed_reason") or state or "нет ответа")
                return status
            elapsed = time.time() - created_at
            self.storage.update_job(job_id, status="running", stage=stage or MUREKA_STATES[state],
                                    progress=min(90, 5 + elapsed / 3))
            if elapsed > JOB_TIMEOUT:
                raise RuntimeError("Mureka не справилась за отведённое время")
            time.sleep(POLL_SECONDS)

    def _start_cover(self, job_id: str) -> None:
        """Обложка рисуется параллельно с музыкой -- к готовой песне она уже есть."""
        job = self.storage.job(job_id)
        settings = job.settings or {}
        if settings.get("coverAsked") or os.path.isfile(os.path.join(self.folder(job_id), "cover.jpg")):
            return
        self.storage.update_job(job_id, settings={**settings, "coverAsked": True})
        task_input = settings.get("input") or {}
        threading.Thread(target=make_cover, daemon=True, args=(
            task_input, job.filename, task_input.get("lyrics", ""), task_input.get("prompt", ""),
            int(job_id[:8], 16))).start()

    def _separate_vocals(self, job_id: str, settings: dict) -> str:
        """Вокал исходника -- Demucs на нашем воркере (режим stems): партии
        приходят в папку задачи по ссылке загрузки, нужна из них одна."""
        folder = self.folder(job_id)
        vocals = os.path.join(folder, "vocals.mp3")
        if os.path.isfile(vocals):
            return vocals
        remote = settings.get("vocals_remote")
        if not remote:
            endpoint = endpoint_id()
            started = call("POST", f"{RUNPOD_API}/{endpoint}/run",
                           {"input": {**settings["input"], "mode": "stems"}})
            remote = settings["vocals_remote"] = {"endpoint": endpoint, "id": started["id"]}
            self.storage.update_job(job_id, settings=settings)
        created = self.storage.job(job_id).created_at
        while True:
            status = call("GET", f"{RUNPOD_API}/{remote['endpoint']}/status/{remote['id']}")
            state = status.get("status")
            if state not in STATES:
                break
            elapsed = time.time() - created
            self.storage.update_job(job_id, status="running", stage="Выделяем ваш голос",
                                    progress=min(40, 3 + elapsed / 4))
            if elapsed > JOB_TIMEOUT:
                raise RuntimeError("видеокарта не справилась за отведённое время")
            time.sleep(POLL_SECONDS)
        if state != "COMPLETED" or not (status.get("output") or {}).get("ok") \
                or not os.path.isfile(vocals):
            raise RuntimeError("не получилось выделить голос: "
                               f"{(status.get('output') or {}).get('error') or state}")
        return vocals

    def _run_keep_vocals(self, job_id: str) -> None:
        """Переделка с голосом оригинала. Remix Mureka перепевает песню сам и
        может увести мелодию припева; здесь мелодия и голос остаются точно
        как были: Demucs выделяет вокал, Mureka track/generate пишет под него
        новую аранжировку (две по очереди: на пробном тарифе одна задача
        за раз), сервер сводит вокал с каждой. Минусы тоже отдаём."""
        try:
            job = self.storage.job(job_id)
            settings = dict(job.settings or {})
            task_input = settings.get("input") or {}
            folder = self.folder(job_id)
            self._start_cover(job_id)
            settings = dict(self.storage.job(job_id).settings or {})
            vocals = self._separate_vocals(job_id, settings)
            state = settings.get("keep") or {"ids": []}
            for number in range(1, KEEP_VARIANTS + 1):
                backing = f"backing_{number}.mp3"
                if os.path.isfile(os.path.join(folder, backing)):
                    continue
                stage = f"Mureka пишет аранжировку {number} из {KEEP_VARIANTS}"
                if len(state["ids"]) < number:
                    self.storage.update_job(job_id, status="running", stage=stage)
                    if not state.get("upload"):
                        mureka_audio(vocals, folder)
                        state["upload"] = mureka_upload_for(
                            task_input, os.path.join(folder, "for_mureka.mp3"), "audio")
                    started = mureka_call("POST", "/v1/track/generate", {
                        "generate_type": "Instrumental", "upload_audio_id": state["upload"],
                        "prompt": task_input.get("prompt", "")[:1024]})
                    if not started.get("id"):
                        raise RuntimeError(f"Mureka не приняла задачу: {str(started)[:200]}")
                    state["ids"].append(started["id"])
                    settings["keep"] = state
                    self.storage.update_job(job_id, settings=settings)
                status = self._mureka_wait(job_id, "song", state["ids"][number - 1],
                                           job.created_at, stage)
                choice = next((c for c in status.get("choices") or [] if c.get("url")), None)
                if not choice:
                    raise RuntimeError("Mureka закончила, но аранжировка до сайта не дошла")
                fetch_result(task_input, choice["url"], folder, backing)
            self.storage.update_job(job_id, status="running", stage="Сводим голос с аранжировкой",
                                    progress=95)
            files = []
            for number in range(1, KEEP_VARIANTS + 1):
                backing = os.path.join(folder, f"backing_{number}.mp3")
                if not os.path.isfile(backing):
                    continue
                name = f"restyle_{number}.mp3"
                mix_vocals(vocals, backing, os.path.join(folder, name))
                files.append({"name": name, "label": label_of("restyle", name)})
            if not files:
                raise RuntimeError("Mureka закончила, но аранжировки до сайта не дошли")
            files += [{"name": f"backing_{n}.mp3", "label": label_of("restyle", f"backing_{n}.mp3")}
                      for n in range(1, KEEP_VARIANTS + 1)
                      if os.path.isfile(os.path.join(folder, f"backing_{n}.mp3"))]
            # Остальные партии Demucs и файл для Mureka больше не нужны.
            for name in os.listdir(folder):
                if name.endswith(".mp3") and name not in {f["name"] for f in files} \
                        and name != "vocals.mp3" and not name.startswith("source."):
                    os.remove(os.path.join(folder, name))
            self.storage.update_job(job_id, status="done", stage="Готово", progress=100, result={
                "files": files, "engine": "mureka", "keepVocals": True})
        except Exception as error:  # noqa: BLE001 -- любая ошибка -> деньги назад
            self._fail(job_id, str(error))

    def _run_mureka(self, job_id: str) -> None:
        """Задача Mureka: переделка (files/upload -> song/remix) или песня с
        нуля (song/generate с текстом, instrumental/generate без него), опрос
        до готовности, результат -- в папку задачи. Задача Mureka хранится
        в задаче сайта: после рестарта опрос продолжается, а не заказывается
        и не оплачивается новая."""
        job = self.storage.job(job_id)
        if ((job.settings or {}).get("input") or {}).get("keep_vocals"):
            self._run_keep_vocals(job_id)
            return
        try:
            settings = dict(job.settings or {})
            task_input = settings.get("input") or {}
            mode = settings.get("mode", "restyle")
            folder = self.folder(job_id)
            self._start_cover(job_id)
            settings = dict(self.storage.job(job_id).settings or {})
            remote = settings.get("remote")
            if not remote:
                self.storage.update_job(job_id, status="running", stage="Отправляем в Mureka",
                                        progress=3)
                remote = self._mureka_start(mode, task_input, folder)
                settings["remote"] = remote
                self.storage.update_job(job_id, settings=settings)
            status = self._mureka_wait(job_id, remote.get("kind", "song"), remote["id"],
                                       job.created_at)
            prefix = "create" if mode == "create" else "restyle"
            files = []
            for number, choice in enumerate(status.get("choices") or [], 1):
                if not choice.get("url"):
                    continue
                name = f"{prefix}_{number}.mp3"
                fetch_result(task_input, choice["url"], folder, name)
                if os.path.isfile(os.path.join(folder, name)):
                    files.append({"name": name, "label": label_of(prefix, name),
                                  "seconds": round((choice.get("duration") or 0) / 1000)})
            if not files:
                raise RuntimeError("Mureka закончила, но версии до сайта не дошли")
            self.storage.update_job(job_id, status="done", stage="Готово", progress=100, result={
                "files": files, "engine": "mureka", "model": status.get("model"),
                "lyrics": task_input.get("lyrics", ""),
                "seconds": (status.get("finished_at") or 0) - (status.get("created_at") or 0)})
        except Exception as error:  # noqa: BLE001 -- любая ошибка -> деньги назад
            self._fail(job_id, str(error))

    def _mureka_start(self, mode: str, task_input: dict, folder: str) -> dict:
        voice = task_input.get("voice", "")
        prompt = ", ".join(p for p in (task_input.get("prompt", ""),
                                       VOICES.get(voice, ("", ""))[1]) if p)[:1024]
        lyrics = task_input.get("lyrics", "")[:5000]
        if mode == "create":
            if lyrics.strip():
                started = mureka_call("POST", "/v1/song/generate", {
                    "lyrics": lyrics, "prompt": prompt, "model": MUREKA_SONG_MODEL, "n": 2,
                    **({"gender": voice} if voice in ("male", "female") else {})})
                kind = "song"
            else:
                started = mureka_call("POST", "/v1/instrumental/generate", {
                    "prompt": task_input.get("prompt", "")[:1024], "model": MUREKA_SONG_MODEL, "n": 2})
                kind = "instrumental"
        else:
            source = next(os.path.join(folder, f) for f in sorted(os.listdir(folder))
                          if f.startswith("source."))
            upload_id = mureka_upload_for(task_input, mureka_source(source, folder), "remix")
            started = mureka_call("POST", "/v1/song/remix", {
                "upload_audio_id": upload_id, "prompt": prompt, "lyrics": lyrics, "n": 2})
            kind = "song"
        if not started.get("id"):
            raise RuntimeError(f"Mureka не приняла задачу: {str(started)[:200]}")
        return {"engine": "mureka", "id": started["id"], "kind": kind}

    def _finish(self, job_id: str, output: dict, status: dict) -> None:
        job = self.storage.job(job_id)
        mode = (job.settings or {}).get("mode", "")
        folder = self.folder(job_id)
        files = []
        for item in output.get("files") or []:
            name = safe_file_name(item.get("name", ""))
            if name and os.path.isfile(os.path.join(folder, name)):
                files.append({"name": name, "label": label_of(mode, name)})
        if not files:
            raise RuntimeError("Воркер закончил, но файлы до сайта не дошли")
        order = list(STEM_LABELS)
        files.sort(key=lambda f: order.index(f["name"][:-4]) if f["name"][:-4] in order else 99)
        self.storage.update_job(job_id, status="done", stage="Готово", progress=100, result={
            "files": files,
            "gpuSeconds": round((status.get("executionTime") or 0) / 1000, 1),
            # С чем работала нейросеть: темп, тональность, модель, крутилки.
            "settings": (output.get("info") or {}).get("settings") or {},
            "waitSeconds": round((status.get("delayTime") or 0) / 1000, 1),
        })

    def _fail(self, job_id: str, reason: str) -> None:
        job = self.storage.job(job_id)
        if job is None:
            return
        charged = float((job.settings or {}).get("charged") or 0)
        by_pack = int((job.settings or {}).get("charged_credit") or 0)
        note = ""
        if job.counted and by_pack:
            self.storage.add_studio_credits(job.user_id, by_pack)
            self.storage.update_job(job_id, counted=False)
            note = " Генерацию вернули в пакет."
        elif job.counted and charged:
            self.storage.add_balance(job.user_id, charged)
            self.storage.update_job(job_id, counted=False)
            note = f" Списанные {charged:.0f} ₽ вернули на баланс."
        self.storage.update_job(job_id, status="error", stage="", error=(
            f"Не получилось: {reason[:300]}.{note}"))
