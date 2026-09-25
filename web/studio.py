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
import time
import urllib.error
import urllib.request
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


SERVICES = {
    "restyle": Service("Переделка в другой стиль", 49.0),
    "enrich": Service("Дописать партию", 39.0),
    "stems": Service("Разделение на партии", 19.0),
}

# Что можно оплатить генерацией из пакета (billing.PLANS studio10/30);
# разделение на партии дешёвое и идёт только с баланса.
PACK_MODES = ("restyle", "enrich")

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
                           f"{error.read()[:300].decode(errors='replace')}") from error
    return json.loads(raw or b"{}")


def api_key() -> str:
    return os.environ.get("RUNPOD_API_KEY", "")


def available() -> tuple[bool, str]:
    if not api_key():
        return False, "Студия ещё не подключена: нет ключа RunPod на сервере"
    return True, ""


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
    """Имя файла от воркера: только простое имя mp3, без путей."""
    return name if re.fullmatch(r"[A-Za-z0-9_.-]{1,80}\.mp3", name or "") else None


def label_of(mode: str, name: str) -> str:
    stem = name.rsplit(".", 1)[0]
    if mode == "stems":
        return STEM_LABELS.get(stem, stem)
    if mode == "enrich":
        return "С дописанной партией"
    number = stem.rsplit("_", 1)[-1]
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
        by_pack = bool((job.settings or {}).get("charged_credit"))
        note = ""
        if job.counted and by_pack:
            self.storage.add_studio_credits(job.user_id, 1)
            self.storage.update_job(job_id, counted=False)
            note = " Генерацию вернули в пакет."
        elif job.counted and charged:
            self.storage.add_balance(job.user_id, charged)
            self.storage.update_job(job_id, counted=False)
            note = f" Списанные {charged:.0f} ₽ вернули на баланс."
        self.storage.update_job(job_id, status="error", stage="", error=(
            f"Не получилось: {reason[:300]}.{note}"))
