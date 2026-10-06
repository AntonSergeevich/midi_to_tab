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

import base64
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
from pathlib import Path

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
    "extend": Service("Продлить песню", 29.0),
    "voice": Service("Мой голос для песен", 49.0),
    "stems_pro": Service("Глубокое разделение + MIDI", 149.0),
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
# Цена в кредитах Студии (billing.PLANS): от себестоимости действия.
# Живые замеры (28.09): продление -- $0.03, песня «как в образце» -- $0.09 за две.
CREDIT_COST = {"create": 10, "restyle": 40, "keep": 20, "enrich": 5, "stems": 3, "extend": 5,
               "voice": 10, "stems_pro": 60}  # глубокое разделение Mureka -- $0.70


# Части песни по-русски -> метки, которые понимают нейросети. Люди пишут
# «[Куплет 1]», «Припев:» -- нейросети учились на [Verse], [Chorus]
RU_TAGS = [(r"пред[- ]?припев", "Pre-Chorus"), (r"куплет", "Verse"), (r"припев", "Chorus"),
           (r"бридж|переход", "Bridge"), (r"вступлени|интро", "Intro"), (r"проигрыш", "Interlude"),
           (r"соло", "Solo"), (r"концовк|кода|аутро|финал", "Outro")]


def english_tags(lyrics: str) -> str:
    out = []
    for line in (lyrics or "").splitlines():
        found = re.fullmatch(r"\s*\[?\s*([А-Яа-яЁё][А-Яа-яЁё -]*?)\s*(\d*)\s*(?::\s*([^\]]*))?\]?\s*:?\s*", line)
        bracketed = line.strip().startswith("[") and line.strip().endswith("]")
        tag = None
        if found and (bracketed or line.strip().endswith(":") or len(line.strip()) < 16):
            word = found.group(1).lower()
            tag = next((name for pattern, name in RU_TAGS if re.match(pattern, word)), None)
        if tag:
            number = f" {found.group(2)}" if found.group(2) else ""
            detail = f": {found.group(3).strip()}" if found.group(3) and found.group(3).strip() else ""
            out.append(f"[{tag}{number}{detail}]")
        else:
            out.append(line)
    return "\n".join(out)


# Стиль по-русски -> английские музыкальные теги. YuE2 учили на английских
# и китайских описаниях: «ню-метал, тяжёлые гитары» она почти не понимает и
# поёт что-то усреднённое (кавер владельца 06.10 вышел «как оригинал»).
# Сначала длинные фразы, потом слова; латиница остаётся как есть.
STYLE_RU = [
    # сленг музыкантов и звукорежиссёров -- раньше общих слов
    ("рэп кор", "rapcore"), ("рэп-кор", "rapcore"), ("рэпкор", "rapcore"), ("хардкор", "hardcore"),
    ("дроп гитар", "drop-tuned guitars"), ("дроп-гитар", "drop-tuned guitars"), ("дроп строй", "drop tuning"),
    ("дроп", "drop"), ("пониженн строй", "downtuned"), ("низк строй", "downtuned"),
    ("расщеплен", "vocal fry screams"), ("фрай", "vocal fry"), ("экстрим вокал", "extreme vocals"),
    ("чист вокал", "clean vocals"), ("речитатив", "rap verses"), ("бэк-вокал", "backing vocals"), ("бэк вокал", "backing vocals"),
    ("бочк", "kick"), ("малый барабан", "snare"), ("тарелк", "cymbals"), ("хай-хэт", "hi-hats"),
    ("хэт", "hi-hats"), ("бласт", "blast beats"), ("двойн бочк", "double kick"), ("кардан", "double kick"),
    ("сэмпл", "samples"), ("семпл", "samples"), ("лупы", "loops"), ("саб-бас", "sub bass"), ("саббас", "sub bass"),
    ("вертушк", "turntable scratches"), ("диджей", "dj scratches"), ("скримо", "screamo"), ("эмо", "emo"),
    ("ню-метал", "nu metal"), ("ню метал", "nu metal"), ("хэви-метал", "heavy metal"), ("хеви-метал", "heavy metal"),
    ("хэви метал", "heavy metal"), ("дэт-метал", "death metal"), ("блэк-метал", "black metal"),
    ("металкор", "metalcore"), ("дэткор", "deathcore"), ("пост-рок", "post-rock"), ("поп-рок", "pop rock"),
    ("панк-рок", "punk rock"), ("поп-панк", "pop punk"), ("инди-рок", "indie rock"), ("инди", "indie"),
    ("альтернатив", "alternative"), ("хард-рок", "hard rock"), ("хард рок", "hard rock"), ("рок-н-ролл", "rock and roll"),
    ("хип-хоп", "hip hop"), ("хип хоп", "hip hop"), ("рэп", "rap"), ("трэп", "trap"), ("дрилл", "drill"),
    ("драм-н-бейс", "drum and bass"), ("драм н бейс", "drum and bass"), ("дабстеп", "dubstep"),
    ("хаус", "house"), ("техно", "techno"), ("транс", "trance"), ("синтвейв", "synthwave"), ("синти-поп", "synth pop"),
    ("электрогитар", "electric guitars"), ("электроник", "electronic"), ("электро", "electro"), ("эмбиент", "ambient"), ("лоу-фай", "lo-fi"), ("лофай", "lo-fi"),
    ("фанк", "funk"), ("соул", "soul"), ("ритм-н-блюз", "r&b"), ("эрнби", "r&b"), ("диско", "disco"),
    ("джаз", "jazz"), ("блюз", "blues"), ("кантри", "country"), ("фолк", "folk"), ("регги", "reggae"),
    ("шансон", "russian chanson"), ("романс", "romance ballad"), ("бардовск", "acoustic singer-songwriter"),
    ("классическ", "classical"), ("оркестр", "orchestral"), ("киномузык", "cinematic"), ("эпичн", "epic"),
    ("баллад", "ballad"), ("поп", "pop"), ("рок", "rock"), ("метал", "metal"), ("панк", "punk"), ("гранж", "grunge"),
    ("семиструн", "7-string"), ("акустическ гитар", "acoustic guitar"),
    ("акустик", "acoustic"), ("тяжёл гитар", "heavy guitars"), ("тяжел гитар", "heavy guitars"),
    ("перегруж", "overdriven"), ("дисторш", "distorted"), ("искаж", "distorted"), ("гитар", "guitars"),
    ("бас-гитар", "bass guitar"), ("бас", "bass"), ("барабан", "drums"), ("ударн", "drums"),
    ("фортепиан", "piano"), ("пианино", "piano"), ("рояль", "grand piano"), ("клавиш", "keys"),
    ("синтезатор", "synthesizer"), ("синт", "synth"), ("скрипк", "violin"), ("струнн", "strings"),
    ("виолончел", "cello"), ("саксофон", "saxophone"), ("труб", "trumpet"), ("духов", "brass"),
    ("аккордеон", "accordion"), ("баян", "accordion"), ("флейт", "flute"), ("скретч", "scratches"),
    ("мужск вокал", "male vocals"), ("мужской голос", "male vocals"), ("женск вокал", "female vocals"),
    ("женский голос", "female vocals"), ("дуэт", "duet"), ("хор", "choir"), ("скрим", "screams"),
    ("гроул", "growls"), ("шёпот", "whisper"), ("шепот", "whisper"), ("мощн вокал", "powerful vocals"),
    ("вокал", "vocals"), ("голос", "vocals"),
    ("агрессивн", "aggressive"), ("мрачн", "dark"), ("тёмн", "dark"), ("темн", "dark"), ("грустн", "sad"),
    ("печальн", "melancholic"), ("меланхол", "melancholic"), ("весёл", "happy"), ("весел", "happy"),
    ("радостн", "joyful"), ("романтичн", "romantic"), ("лиричн", "lyrical"), ("нежн", "gentle"),
    ("мягк", "soft"), ("спокойн", "calm"), ("энергичн", "energetic"), ("драйв", "driving"), ("мощн", "powerful"),
    ("тяжёл", "heavy"), ("тяжел", "heavy"), ("жёстк", "hard"), ("жестк", "hard"), ("лёгк", "light"), ("легк", "light"),
    ("танцевальн", "danceable"), ("ностальг", "nostalgic"), ("атмосферн", "atmospheric"), ("гимн", "anthemic"),
    ("быстр", "fast tempo"), ("медленн", "slow tempo"), ("средн темп", "mid tempo"),
    ("мелодичн", "melodic"), ("рифф", "riffs"), ("соло", "solo"), ("брейкдаун", "breakdown"),
    ("куплет", "verses"), ("припев", "chorus"),
    ("80-х", "80s"), ("90-х", "90s"), ("2000-х", "2000s"), ("ретро", "retro"), ("русск", "russian"),
]


def english_style(style: str) -> str:
    """Описание стиля -> английские теги для YuE2: известные русские
    музыкальные слова переводятся, латиница остаётся, прочая кириллица
    отбрасывается (её модель всё равно не поймёт)."""
    text = (style or "").strip()
    if not re.search(r"[А-Яа-яЁё]", text):
        return text
    tags = []
    for chunk in re.split(r"[,;.\n]+", text.lower()):
        chunk = chunk.strip()
        if not chunk:
            continue
        found = []
        for ru, en in STYLE_RU:
            # фраза из нескольких основ: все основы должны встретиться по порядку
            pattern = r"\b" + r"[а-яё-]*\s+".join(map(re.escape, ru.split())) + r"[а-яё-]*"
            if re.search(pattern, chunk):
                found.append((re.search(pattern, chunk).start(), en))
                chunk = re.sub(pattern, " ", chunk)
        found.sort()
        latin = re.findall(r"[a-z0-9][a-z0-9&'+\- ]*[a-z0-9]|[a-z0-9]", chunk)
        words = [en for _, en in found] + [w.strip() for w in latin if w.strip()]
        if words:
            tags.append(" ".join(dict.fromkeys(words)))
    return ", ".join(dict.fromkeys(tags))[:500] or text


def voice_clone_open() -> bool:
    """Клон голоса Mureka на нашем тарифе API не включён (ответ 429 «exceeded
    your current quota» при деньгах на счёте) -- включается переменной, когда
    Mureka откроет song/vocal-clone."""
    return os.environ.get("NASLUX_VOICE_CLONE", "") == "1"


def credit_cost(mode: str, keep_vocals: bool = False) -> int:
    return CREDIT_COST["keep"] if mode == "restyle" and keep_vocals else CREDIT_COST.get(mode, 0)
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
    # Глубокое разделение Mureka (audio-separation-2): до 12 партий
    "vocal": "Вокал", "flute": "Флейта", "fx": "Эффекты", "strings": "Струнные",
    "synth": "Синтезатор", "keyboard": "Клавишные", "brass": "Духовые",
    "woodwinds": "Деревянные духовые", "brass_and_woodwinds": "Духовые", "instrumental": "Минус",
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


# Деньги на счету Mureka кончились -- сайт не ломается: деньги за задачу
# возвращаются, генерация показана «временно недоступной» (или, если
# включено ace_fallback, уходит на свой ACE-Step), остальная Студия
# работает. Через полчаса Mureka пробуется снова: пополнили счёт -- всё
# вернулось само.
MUREKA_RECHECK = 30 * 60
_mureka_broke = {"at": 0.0, "why": "", "file": ""}


class MurekaNoMoney(RuntimeError):
    """Mureka отказала из-за денег на счёте, а не из-за очереди."""


def mureka_broke() -> bool:
    if os.environ.get("NASLUX_MUREKA_OFF", "") == "1":
        return True
    if not _mureka_broke["at"] and _mureka_broke["file"]:
        try:
            with open(_mureka_broke["file"]) as f:
                _mureka_broke.update({k: v for k, v in json.load(f).items() if k in ("at", "why")})
        except (OSError, ValueError):
            _mureka_broke["at"] = -1.0          # файла нет -- не читать его каждый раз
    return time.time() - max(_mureka_broke["at"], 0) < MUREKA_RECHECK


def mark_mureka_broke(why: str) -> None:
    _mureka_broke.update(at=time.time(), why=why[:300])
    print(f"[mureka] кончились деньги, переходим на ACE-Step: {why[:200]}", file=sys.stderr, flush=True)
    if _mureka_broke["file"]:
        try:
            with open(_mureka_broke["file"], "w") as f:
                json.dump({"at": _mureka_broke["at"], "why": _mureka_broke["why"]}, f)
        except OSError:
            pass


def mark_mureka_ok() -> None:
    if _mureka_broke["at"] > 0:
        _mureka_broke["at"] = 0.0
        if _mureka_broke["file"]:
            try:
                os.remove(_mureka_broke["file"])
            except OSError:
                pass


def ace_fallback() -> bool:
    """Подменять Mureka своим ACE-Step, когда у неё нет денег. Выключено:
    владелец послушал песни с нуля на ACE-Step (05.10) -- «сбился такт»,
    «хуже Mureka и намного хуже Suno»; переделку на нём он забраковал ещё
    раньше. Продавать такое за ту же цену нельзя -- услуга честно
    «временно недоступна». NASLUX_ACE_FALLBACK=1 включает подмену."""
    return bool(api_key()) and os.environ.get("NASLUX_ACE_FALLBACK", "0") == "1"


def ace_create_open() -> bool:
    """Песни с нуля на ACE-Step (text2music) -- только если включена подмена."""
    return ace_fallback()


def create_open() -> bool:
    return restyle_engine() == "mureka" or ace_create_open()


def restyle_engine() -> str:
    """Переделка и песни с нуля -- Mureka, если на сервере есть её ключ.

    NASLUX_RESTYLE_ENGINE=runpod возвращает переделку на ACE-Step (для
    проб). С российского сервера Mureka напрямую не отвечает (403 «Service
    unavailable»), поэтому запросы к ней идут через ретранслятор на RunPod
    (relay/) -- см. use_relay()."""
    wanted = os.environ.get("NASLUX_RESTYLE_ENGINE", "mureka").strip().lower()
    return "mureka" if wanted == "mureka" and mureka_key() and not mureka_broke() else "runpod"


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
        raise RuntimeError(f"ретранслятор: {job.get('status')} {short_error(str(job.get('error')))}")
    return job.get("output") or {}


def runpod_restyle_recipe(audio_influence: float, melody: float) -> dict:
    """Рецепт переделки на RunPod поверх умолчаний воркера (поле raw).

    Живые пробы владельца на we_angel: с подмешиванием исходного звука
    (cover_noise_strength, «удержание мелодии») новые инструменты звучали
    поверх оригинала; без него -- как переписанные партии, и лучшей
    вышла версия с силой исходника 0.5 (nomix_v2). Поэтому удержание по
    умолчанию 0, а человек может добавить его сам (0..0.25) -- ближе к
    мелодии ценой того самого наложения."""
    return {"raw": {"audio_cover_strength": round(max(0.1, audio_influence), 3),
                    "cover_noise_strength": round(0.25 * melody, 3)}}


def restyle_open() -> bool:
    # Пока Mureka без денег -- переделка на ACE-Step открыта всем: лучше
    # чуть проще звук, чем закрытая услуга
    return restyle_engine() == "mureka" or RESTYLE_OPEN or (bool(mureka_key()) and mureka_broke()
                                                            and ace_fallback())


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
            out = relay_run({"op": "call", "method": method, "path": path, "body": body,
                             "timeout": max(timeout, 300)})
            if out.get("ok"):
                mark_mureka_ok()
                return out.get("json") or {}
            code, detail = out.get("status") or 0, out.get("error") or ""
        else:
            code, detail, answer = _mureka_direct(method, path, body, timeout)
            if answer is not None:
                mark_mureka_ok()
                return answer
        money = any(word in detail.lower() for word in MUREKA_NOT_BUSY)
        if money and code in (400, 402, 403, 429):
            mark_mureka_broke(detail)
            raise MurekaNoMoney(f"Mureka ответила {code}: {short_error(detail)}")
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


def mureka_upload_for(task_input: dict, path: str, purpose: str, api_path: str = "",
                      fields: dict | None = None, key: str = "id") -> str:
    """Загрузить файл задачи в Mureka: через ретранслятор -- по подписанной
    ссылке сайта (task_input["mureka_url"] отдаёт for_mureka.mp3), напрямую --
    с диска. api_path/fields/key -- для приёмов файла помимо files/upload."""
    if not use_relay():
        if api_path:
            return mureka_upload(path, purpose, api_path, fields, key)
        return mureka_upload(path, purpose)
    out = relay_run({"op": "upload", "source_url": task_input["mureka_url"],
                     "purpose": purpose, "filename": "track.mp3",
                     **({"path": api_path, "fields": fields} if api_path else {})})
    if not out.get("ok") or not (out.get("json") or {}).get(key):
        raise RuntimeError(f"Mureka не приняла файл ({out.get('status')}): "
                           f"{short_error(out.get('error') or str(out.get('json')))}")
    return out["json"][key]


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


def mureka_upload(path: str, purpose: str, api_path: str = "/v1/files/upload",
                  fields: dict | None = None, key: str = "id") -> str:
    """Файл в Mureka multipart-запросом: files/upload (поле purpose) или
    другой приём файла (song/vocal-clone с description). Возвращает id."""
    boundary = uuid.uuid4().hex
    with open(path, "rb") as f:
        content = f.read()
    name = os.path.basename(path)
    kind = "audio/midi" if name.lower().endswith((".mid", ".midi")) else "audio/mpeg"
    head = "".join(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n"
                   for k, v in (fields or {"purpose": purpose}).items())
    body = (head + f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
            f"filename=\"{name}\"\r\nContent-Type: {kind}\r\n\r\n").encode() \
        + content + f"\r\n--{boundary}--\r\n".encode()
    request = urllib.request.Request(f"{MUREKA_API}{api_path}", data=body, method="POST",
                                     headers={"Authorization": f"Bearer {mureka_key()}",
                                              "Content-Type": f"multipart/form-data; boundary={boundary}",
                                              "User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            uploaded = json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"Mureka не приняла файл ({error.code}): "
                           f"{short_error(error.read()[:2000].decode(errors='replace'))}") from error
    if not uploaded.get(key):
        raise RuntimeError(f"Mureka не вернула {key}: {str(uploaded)[:200]}")
    return uploaded[key]


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


def lyrics_from_sections(recognized: dict) -> str:
    """Ответ song/recognize -> текст с [Verse]-пометками по секциям."""
    parts = []
    for section in recognized.get("lyrics_sections") or []:
        lines = [l.get("text", "").strip() for l in section.get("lines") or []]
        lines = [l for l in lines if l]
        if lines:
            parts.append("[Verse]\n" + "\n".join(lines))
    return "\n\n".join(parts)


def recognize_lyrics(task_input: dict, path: str) -> str:
    """Слова песни с записи (Mureka song/recognize). Нет слов -- понятная ошибка."""
    audio_id = mureka_upload_for(task_input, path, "audio")
    text = lyrics_from_sections(mureka_call("POST", "/v1/song/recognize",
                                            {"upload_audio_id": audio_id}, timeout=600))
    if not text.strip():
        raise RuntimeError("не удалось разобрать слова песни — вставьте текст в поле «Текст песни»")
    return text[:3000]


def style_from_description(described: dict) -> str:
    """Ответ song/describe -> строка стиля для поля «Стиль»: жанры,
    инструменты, настроение -- без повторов, как пишут в Suno/Mureka."""
    words: list[str] = []
    for part in [*(described.get("genres") or []), *(described.get("instrument") or []),
                 *(described.get("tags") or [])]:
        word = str(part).strip().lower()
        if word and word not in words:
            words.append(word)
    return ", ".join(words)[:400]


def describe_style(path: str, folder: str) -> dict:
    """Стиль песни (Mureka song/describe): ей хватает минуты из середины,
    base64 до 10 МБ -- отдаём кусок mp3 128 кбит/с (~1 МБ) прямо в запросе."""
    snippet = os.path.join(folder, "describe.mp3")
    start = 0.0
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                             capture_output=True, text=True, timeout=60)
        start = max(0.0, float(out.stdout.strip() or 0) * 0.25)
    except (OSError, ValueError, subprocess.SubprocessError):
        start = 0.0
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{start:.1f}", "-i", path, "-t", "75",
                    "-ac", "2", "-b:a", "128k", snippet], check=True, timeout=300)
    try:
        data = base64.b64encode(open(snippet, "rb").read()).decode()
    finally:
        os.remove(snippet)
    described = mureka_call("POST", "/v1/song/describe", {"url": f"data:audio/mp3;base64,{data}"}, timeout=300)
    return {"style": style_from_description(described),
            "description": str(described.get("description") or "")[:600]}


# Пометки голоса в строках текста: (Male), (Female voice, fast rap), (Мужской)...
VOICE_MARK = re.compile(r"^\s*\((?:[^)]*?)\b(male|female|together|both|duet|мужск\w*|женск\w*|вместе|дуэт)\b[^)]*\)\s*",
                        re.IGNORECASE)


def voice_plan(voice: str, lyrics: str) -> tuple[str, str, str | None]:
    """Голос для song/generate -> (голос, текст, gender).

    Живые пробы (владелец, 27.09): без gender Mureka поёт мужским голосом,
    даже если в тексте расписаны партии (Male)/(Female); с gender=female
    и такой разметкой -- поёт дуэтом. Отсюда:
      мужской/женский -- чужие пометки из текста убираем, иначе выйдет дуэт;
      дуэт (и «любой» с расписанными партиями) -- gender=female и пометки,
      а если их нет, расставляем сами: куплеты по очереди, припев вместе;
      любой без пометок -- решает Mureka."""
    marked = any(VOICE_MARK.match(line) for line in lyrics.splitlines())
    if voice in ("male", "female"):
        clean = "\n".join(VOICE_MARK.sub("", line) for line in lyrics.splitlines())
        return voice, clean, voice
    if voice == "duet" or (voice == "" and marked):
        return "duet", lyrics if marked else mark_duet(lyrics), "female"
    return voice, lyrics, None


def mark_duet(lyrics: str) -> str:
    """Расставить партии: куплеты -- по очереди мужской/женский, припев и
    финал -- вместе, прочее -- как предыдущий куплет."""
    out, verse, current = [], 0, "(Male)"
    for line in lyrics.splitlines():
        head = line.strip().lower()
        if head.startswith("["):
            if "chorus" in head or "припев" in head or "outro" in head or "финал" in head:
                current = "(Together)"
            elif "verse" in head or "куплет" in head:
                current = "(Male)" if verse % 2 == 0 else "(Female)"
                verse += 1
            out.append(line)
        elif line.strip():
            out.append(f"{current} {line.strip()}")
        else:
            out.append(line)
    return "\n".join(out)


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


def shift_audio(source: str, target: str, semitones: int, tempo: float) -> None:
    """Сдвиг тональности (полутоны) и темпа (1.0 = как было) без «эффекта
    бурундука»: rubberband сохраняет тембр. Если ffmpeg собран без него --
    передискретизация (asetrate) и выравнивание длины atempo."""
    ratio = 2 ** (semitones / 12)
    good = f"rubberband=pitch={ratio:.6f}:tempo={tempo:.4f}"
    try:
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", source, "-af", good, "-b:a", "256k", target],
                       check=True, timeout=600, capture_output=True)
        return
    except subprocess.CalledProcessError:
        pass
    speed = tempo / ratio          # после asetrate темп вырос в ratio раз -- возвращаем
    chain = []
    while speed < 0.5:
        chain.append("atempo=0.5")
        speed /= 0.5
    while speed > 2.0:
        chain.append("atempo=2.0")
        speed /= 2.0
    chain.append(f"atempo={speed:.5f}")
    filters = f"asetrate=44100*{ratio:.6f},aresample=44100," + ",".join(chain)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", source, "-ar", "44100", "-af", filters,
                    "-b:a", "256k", target], check=True, timeout=600)


def shift_label(semitones: int, tempo: float) -> str:
    parts = []
    if semitones:
        parts.append(f"{'+' if semitones > 0 else '−'}{abs(semitones)} полутон"
                     + ("а" if abs(semitones) in (2, 3, 4) else "" if abs(semitones) == 1 else "ов"))
    if round(tempo * 100) != 100:
        parts.append(f"темп {round(tempo * 100)}%")
    return ", ".join(parts) or "без изменений"


def download(url: str, target: str) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=300) as response, open(target, "wb") as out:
        shutil.copyfileobj(response, out)


_endpoint_cache: dict[str, str] = {}


def endpoint_id(name: str = WORKER_NAME) -> str:
    """ID эндпоинта: из окружения или по имени через API RunPod (один раз)."""
    configured = os.environ.get("NASLUX_WORKER_ENDPOINT" if name == WORKER_NAME else "NASLUX_YUE2_ENDPOINT", "")
    if configured:
        return configured
    if name not in _endpoint_cache:
        found = [e for e in call("GET", f"{RUNPOD_REST}/endpoints")
                 if e.get("name", "").startswith(name)]
        if not found:
            raise RuntimeError(f"На RunPod нет эндпоинта {name}")
        _endpoint_cache[name] = found[0]["id"]
    return _endpoint_cache[name]


# YuE2 (M-A-P): песни с нуля и каверы уровня Suno, свой эндпоинт на RunPod
# (yue2/). Веса под CC BY-NC 4.0 -- движок виден только владельцу
# (безлимит) для некоммерческой пробы; покупателям -- после лицензии M-A-P.
YUE2_NAME = "naslux-yue2"
# MIDI частей от SheetSage2 («Ноты, аккорды и MIDI»)
MIDI_LABELS = {"melody_vocal": "Мелодия вокала", "melody_instrumental": "Мелодия инструментов",
               "melody": "Мелодия: вокал и инструменты",
               "chords": "Аккорды", "transcription": "Мелодия и аккорды вместе"}


def yue2_open(user) -> bool:
    return bool(api_key()) and bool(getattr(user, "unlimited", False)) \
        and os.environ.get("NASLUX_YUE2", "1") == "1"


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


def audio_seconds(path: str) -> int:
    """Длительность записи в секундах (ffprobe; нет его -- 0)."""
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                              "-of", "default=nw=1:nk=1", path], capture_output=True, text=True, timeout=60)
        return round(float(out.stdout.strip() or 0))
    except (OSError, ValueError, subprocess.SubprocessError):
        return 0


# Версия разбора: сменилась (модель, подсказка тональности) -- старый кеш
# в задачах пересчитывается при следующем открытии
ANALYSIS_VERSION = 10


def analyze_audio(path: str) -> dict:
    """Тональность, темп, доли и аккорды записи -- для карточки трека,
    окна «Темп и тональность» и метронома мультитрека. Тот же разбор, что
    у подбора аккордов (нейросеть), минуты на сервере не тратятся зря:
    результат хранится в задаче."""
    from midi2tab import audiochords

    from midi2tab import beatnet

    found = audiochords.detect_from_audio(path)
    return {"v": ANALYSIS_VERSION, "key": found.key, "bpm": round(found.tempo) if found.tempo else None,
            "tempoBy": "net" if beatnet.available() else "librosa",
            "beats": [round(b, 3) for b in found.beats],
            "downbeats": [round(b, 3) for b in found.downbeats],
            "chords": [[round(c.start, 2), round(c.end, 2), c.name] for c in found.chords]}


def with_tempo(analysis: dict, fix: dict | None) -> dict:
    """Темп, который музыкант поправил сам: ровная сетка долей от его
    сильной доли, такт -- четыре доли. Автоопределение ошибается на
    «качающихся» песнях (81 читается как 108), а метроному нужна точность."""
    if not fix or not fix.get("bpm") or analysis.get("error"):
        return analysis
    step = 60.0 / fix["bpm"]
    start = float(fix.get("start") or 0.0)
    ends = [b for b in analysis.get("beats") or []] + [c[1] for c in analysis.get("chords") or []]
    end = max(ends, default=start) + step
    first = start - step * int(start / step)
    beats = [round(first + k * step, 3) for k in range(int((end - first) / step) + 1)]
    downbeats = [b for k, b in enumerate(beats) if (k - round((start - first) / step)) % 4 == 0]
    return {**analysis, "bpm": round(fix["bpm"], 1) if fix["bpm"] % 1 else int(fix["bpm"]),
            "beats": beats, "downbeats": downbeats, "autoBpm": analysis.get("bpm"), "fixed": True}


def safe_file_name(name: str) -> str | None:
    """Имя файла от воркера: простое имя mp3/mid/zip (или обложка), без путей."""
    if name == "cover.jpg":
        return name
    return name if re.fullmatch(r"[A-Za-z0-9_-][A-Za-z0-9_.-]{0,80}\.(mp3|mid|zip)", name or "") else None


def label_of(mode: str, name: str) -> str:
    stem = name.rsplit(".", 1)[0]
    if mode == "stems":
        return STEM_LABELS.get(stem, stem)
    if mode == "enrich":
        return "С дописанной партией"
    number = stem.rsplit("_", 1)[-1]  # restyle_2 / create_2 / backing_2
    if stem.startswith("extend_"):
        return f"Продолжение {number}"
    if stem.startswith("backing_"):
        return f"Минус {number} — без голоса"
    return f"Вариант {number}" if number.isdigit() else "Новая версия"


# ------------------------------------------------------------------ задачи

# Разбор звука (аккорды, доли, сверка с эталоном) идёт на самом сайте, а у
# сервера 4 ГБ. Три сверки подряд (каждая -- песня целиком через четыре
# нейросети) упёрли службу в предел памяти, и сайт перестал отвечать
# (05.10). Тяжёлый разбор -- строго по одному, остальные ждут очереди.
HEAVY = threading.Semaphore(int(os.environ.get("NASLUX_HEAVY_SLOTS", "1")))
# Сверки -- ещё и в своей очереди: «пересверить все» ставит их пачкой, и
# разбор трека человека ждёт не больше одной сверки, а не всю пачку.
BENCH = threading.Semaphore(1)


class StudioRunner:
    """Ставит задачи на RunPod и следит за ними в фоне."""

    def __init__(self, storage: Storage, data_dir: str) -> None:
        _mureka_broke.update(file=os.path.join(data_dir, "mureka_broke.json"), at=0.0)
        self.storage = storage
        self.data_dir = data_dir
        self.pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="studio")
        self._analysis_lock = threading.Lock()
        # MIDI отдельной партии -- по одной за раз: Basic Pitch ест процессор
        self.midi_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="midi")
        self.transcribing: set[tuple[str, str]] = set()
        self._analyzing: set[tuple[str, str]] = set()

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
        if job and (job.settings or {}).get("engine") == "local":
            self._run_local(job_id)
            return
        try:
            job = self.storage.job(job_id)
            settings = dict(job.settings or {})
            remote = settings.get("remote")
            if not remote:
                endpoint = endpoint_id(YUE2_NAME if settings.get("engine") == "yue2" else WORKER_NAME)
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

    def _run_local(self, job_id: str) -> None:
        """Работа на своём сервере без нейросетей: темп и тональность."""
        try:
            job = self.storage.job(job_id)
            settings = job.settings or {}
            folder = self.folder(job_id)
            self.storage.update_job(job_id, status="running", stage="Подкручиваем колки и метроном",
                                    progress=30)
            if settings.get("mode") == "upload":
                self._run_upload(job_id, folder)
                return
            semitones, tempo = int(settings.get("semitones", 0)), float(settings.get("tempo", 1.0))
            if settings.get("batch"):
                # Все дорожки мультитрека: те же имена и подписи, что у партий
                files = []
                for n, part in enumerate(settings["batch"], 1):
                    shift_audio(os.path.join(folder, f"src_{part['name']}"), os.path.join(folder, part["name"]),
                                semitones, tempo)
                    os.remove(os.path.join(folder, f"src_{part['name']}"))
                    files.append({"name": part["name"], "label": part["label"]})
                    self.storage.update_job(job_id, status="running", progress=10 + 85 * n // len(settings["batch"]),
                                            stage=f"Подкручиваем колки: {n} из {len(settings['batch'])}")
                self.storage.update_job(job_id, status="done", stage="Готово", progress=100,
                                        result={"files": files})
                return
            shift_audio(os.path.join(folder, "source.mp3"), os.path.join(folder, "shifted.mp3"),
                        semitones, tempo)
            self.storage.update_job(job_id, status="done", stage="Готово", progress=100, result={
                "files": [{"name": "shifted.mp3", "label": shift_label(semitones, tempo)}]})
        except Exception as error:  # noqa: BLE001
            what = "подготовить трек" if (self.storage.job(job_id).settings or {}).get("mode") == "upload" \
                else "изменить темп и тон"
            self._fail(job_id, f"не получилось {what}: {error}")

    def _run_upload(self, job_id: str, folder: str) -> None:
        """Свой трек в «Мои треки»: mp3 для плеера и мультитрека, сразу же --
        тональность, темп и доли."""
        source = next(os.path.join(folder, f) for f in sorted(os.listdir(folder)) if f.startswith("source."))
        target = os.path.join(folder, "track.mp3")
        self.storage.update_job(job_id, status="running", stage="Настраиваем звук", progress=40)
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", source, "-ac", "2", "-b:a", "192k", target],
                       check=True, timeout=600)
        self.storage.update_job(job_id, status="done", stage="Готово", progress=100, result={
            "files": [{"name": "track.mp3", "label": "Оригинал", "seconds": audio_seconds(target)}]})
        self.analyze(job_id, "track.mp3")

    def analyze(self, job_id: str, name: str) -> dict:
        """Разбор версии (тональность, темп, доли, аккорды) с кешем в задаче."""
        job = self.storage.job(job_id)
        cached = ((job.result or {}).get("analysis") or {}).get(name) if job else None
        if cached and cached.get("v") == ANALYSIS_VERSION:
            return cached
        try:
            with HEAVY:
                found = analyze_audio(os.path.join(self.folder(job_id), name))
        except Exception as error:  # noqa: BLE001 -- разбор -- не повод ронять задачу
            found = {"error": str(error)[:200]}
        if name == "harmony.wav" and "error" not in found:
            found = self._with_source_rhythm(job, found)
        with self._analysis_lock:
            job = self.storage.job(job_id)
            result = dict(job.result or {})
            result["analysis"] = {**(result.get("analysis") or {}), name: found}
            self.storage.update_job(job_id, result=result)
        return found

    def _with_source_rhythm(self, job, found: dict) -> dict:
        """Темп и доли -- по треку целиком, аккорды -- по партиям без голоса
        и барабанов. Без барабанов доли плывут (96 BPM читалось как 129), и
        мультитрек расходился со Студией и с метрономом."""
        settings = job.settings or {}
        parent, source = settings.get("from"), settings.get("sourceFile")
        if parent and source and os.path.isfile(os.path.join(self.folder(parent), source)):
            rhythm = self.analyze(parent, source)
        else:
            folder = self.folder(job.id)
            own = next((f for f in sorted(os.listdir(folder)) if f.startswith("source.")), None) \
                if os.path.isdir(folder) else None
            rhythm = self.analyze(job.id, own) if own else {}
        if rhythm.get("beats"):
            found = {**found, "bpm": rhythm.get("bpm"), "beats": rhythm["beats"],
                     "downbeats": rhythm.get("downbeats") or []}
        return found

    def transcribe_later(self, job_id: str, name: str) -> bool:
        """MIDI партии нашей расшифровкой нот (Basic Pitch) -- в фоне, в список
        MIDI той же работы. False -- уже в работе."""
        key = (job_id, name)
        with self._analysis_lock:
            if key in self.transcribing:
                return False
            self.transcribing.add(key)

        def work():
            try:
                from midi2tab import audioin

                folder = self.folder(job_id)
                target = f"{Path(name).stem}.mid"
                audioin.transcribe(os.path.join(folder, name), os.path.join(folder, target))
                with self._analysis_lock:
                    job = self.storage.job(job_id)
                    result = dict(job.result or {})
                    label = label_of((job.settings or {}).get("mode", ""), name)
                    midi = [m for m in result.get("midi") or [] if m["name"] != target]
                    result["midi"] = midi + [{"name": target, "label": label}]
                    self.storage.update_job(job_id, result=result)
            except Exception as error:  # noqa: BLE001
                print(f"[midi] {job_id}/{name}: {error}", file=sys.stderr, flush=True)
            finally:
                with self._analysis_lock:
                    self.transcribing.discard(key)

        self.midi_pool.submit(work)
        return True

    def describe(self, job_id: str, name: str, mureka_url: str) -> dict:
        """Стиль и слова готового трека -- для «Повторить» и «Кавер на этот
        трек»: стиль -- song/describe, слова -- сохранённые при создании или
        song/recognize. Кэш -- в задаче, второй раз Mureka не зовём."""
        job = self.storage.job(job_id)
        folder = self.folder(job_id)
        found: dict = {"v": 1}
        try:
            prepared = mureka_source(os.path.join(folder, name), folder)
            try:
                found.update(describe_style(prepared, folder))
            except Exception as error:  # noqa: BLE001 -- без стиля слова всё равно нужны
                found["styleError"] = short_error(str(error))
            settings = job.settings or {}
            stored = ((job.result or {}).get("lyrics") or (settings.get("input") or {}).get("lyrics") or "").strip()
            if stored and settings.get("mode") != "upload":
                found["lyrics"] = stored
            else:
                try:
                    found["lyrics"] = recognize_lyrics({"mureka_url": mureka_url}, prepared)
                except RuntimeError:
                    found["lyrics"] = ""        # инструментал или слов не разобрать
        except Exception as error:  # noqa: BLE001
            found = {"v": 1, "error": short_error(str(error))}
        with self._analysis_lock:
            job = self.storage.job(job_id)
            result = dict(job.result or {})
            result["described"] = {**(result.get("described") or {}), name: found}
            self.storage.update_job(job_id, result=result)
        return found

    def benchmark(self, job_id: str, name: str) -> dict:
        """Сверка с эталоном: трек слушают все модели аккордов, что есть на
        сервере; меры -- в web/benchmark.py. Итог -- рядом с эталоном."""
        from . import benchmark as bench

        job = self.storage.job(job_id)
        entry = dict(((job.result or {}).get("reference") or {}).get(name) or {})
        try:
            with BENCH, HEAVY:
                runs = bench.run_models(os.path.join(self.folder(job_id), name))
            entry["key"] = runs.pop("_key", None)
            entry["tuning"] = runs.pop("_tuning", None)
            entry["scores"] = {model: bench.compare(entry.get("chords") or [], segments)
                               for model, segments in runs.items()}
            entry.pop("error", None)
        except Exception as error:  # noqa: BLE001
            entry["error"] = short_error(str(error))
        entry["pending"] = False
        with self._analysis_lock:
            job = self.storage.job(job_id)
            result = dict(job.result or {})
            result["reference"] = {**(result.get("reference") or {}), name: entry}
            self.storage.update_job(job_id, result=result)
        return entry

    def benchmarking(self, job_id: str, name: str) -> bool:
        with self._analysis_lock:
            return (job_id, "bench:" + name) in self._analyzing

    def benchmark_later(self, job_id: str, name: str) -> bool:
        key = (job_id, "bench:" + name)
        with self._analysis_lock:
            if key in self._analyzing:
                return False
            self._analyzing.add(key)

        def work():
            try:
                self.benchmark(job_id, name)
            finally:
                with self._analysis_lock:
                    self._analyzing.discard(key)

        threading.Thread(target=work, daemon=True).start()
        return True

    def describe_later(self, job_id: str, name: str, mureka_url: str) -> bool:
        key = (job_id, "describe:" + name)
        with self._analysis_lock:
            if key in self._analyzing:
                return False
            self._analyzing.add(key)

        def work():
            try:
                self.describe(job_id, name, mureka_url)
            finally:
                with self._analysis_lock:
                    self._analyzing.discard(key)

        threading.Thread(target=work, daemon=True).start()
        return True

    def analyze_later(self, job_id: str, name: str) -> bool:
        """Запустить разбор в фоне; False -- он уже идёт."""
        key = (job_id, name)
        with self._analysis_lock:
            if key in self._analyzing:
                return False
            self._analyzing.add(key)

        def work():
            try:
                self.analyze(job_id, name)
            finally:
                with self._analysis_lock:
                    self._analyzing.discard(key)

        threading.Thread(target=work, daemon=True).start()
        return True

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

    def _run_stems_pro(self, job_id: str) -> None:
        """Глубокое разделение Mureka (audio-separation-2): до 12 партий в wav
        и MIDI каждой. Партии -- в mp3 для прослушивания, MIDI -- отдельными
        файлами (из них табы собираются без распознавания звука) и архивом."""
        import zipfile

        try:
            job = self.storage.job(job_id)
            task_input = (job.settings or {}).get("input") or {}
            folder = self.folder(job_id)
            self.storage.update_job(job_id, status="running", stage="Раскладываем на 12 дорожек",
                                    progress=10)
            source = next(os.path.join(folder, f) for f in sorted(os.listdir(folder))
                          if f.startswith("source."))
            # data URI до 10 МБ: base64 раздувает на треть -- кодируем в 128 кбит/с
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", source, "-t", str(MUREKA_MAX_SECONDS),
                            "-ac", "2", "-b:a", "128k", os.path.join(folder, "for_mureka.mp3")],
                           check=True, timeout=300)
            if use_relay():
                out = relay_run({"op": "stem", "source_url": task_input["mureka_url"],
                                 "model": "audio-separation-2"}, timeout=1200)
                if not out.get("ok"):
                    raise RuntimeError(f"Mureka не разделила ({out.get('status')}): "
                                       f"{short_error(out.get('error') or '')}")
                answer = out.get("json") or {}
            else:
                import base64

                data = base64.b64encode(open(os.path.join(folder, "for_mureka.mp3"), "rb").read()).decode()
                answer = mureka_call("POST", "/v1/song/stem", {
                    "url": f"data:audio/mp3;base64,{data}", "model": "audio-separation-2"}, timeout=900)
            self.storage.update_job(job_id, status="running", stage="Забираем партии и MIDI", progress=70)
            for key, name in (("zip_url", "stems.zip"), ("midi_zip_url", "midi.zip")):
                if answer.get(key):
                    fetch_result(task_input, answer[key], folder, name)
            files, midi = [], []
            stems_zip = os.path.join(folder, "stems.zip")
            if os.path.isfile(stems_zip):
                with zipfile.ZipFile(stems_zip) as archive:
                    for entry in archive.namelist():
                        stem = re.sub(r"[^a-z0-9]+", "_", Path(entry).stem.lower()).strip("_")
                        if not entry.lower().endswith((".wav", ".mp3")) or not stem:
                            continue
                        raw = os.path.join(folder, f"raw_{stem}{Path(entry).suffix.lower()}")
                        with archive.open(entry) as src, open(raw, "wb") as dst:
                            shutil.copyfileobj(src, dst)
                        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", raw, "-b:a", "256k",
                                        os.path.join(folder, f"{stem}.mp3")], check=True, timeout=300)
                        os.remove(raw)
                        files.append({"name": f"{stem}.mp3", "label": STEM_LABELS.get(stem, stem.capitalize())})
            midi_zip = os.path.join(folder, "midi.zip")
            if os.path.isfile(midi_zip):
                with zipfile.ZipFile(midi_zip) as archive:
                    for entry in archive.namelist():
                        stem = re.sub(r"[^a-z0-9]+", "_", Path(entry).stem.lower()).strip("_")
                        if entry.lower().endswith((".mid", ".midi")) and stem:
                            with archive.open(entry) as src, open(os.path.join(folder, f"{stem}.mid"), "wb") as dst:
                                shutil.copyfileobj(src, dst)
                            midi.append({"name": f"{stem}.mid",
                                         "label": f"MIDI · {STEM_LABELS.get(stem, stem.capitalize())}"})
            if not files and not midi:
                raise RuntimeError("Mureka закончила, но партии до сайта не дошли")
            if files and os.path.isfile(stems_zip):
                # Архив Mureka -- WAV, сотни мегабайт: для скачивания пересобираем
                # его из наших mp3 (в разы меньше), исходник не храним
                os.remove(stems_zip)
                with zipfile.ZipFile(stems_zip, "w", zipfile.ZIP_STORED) as archive:
                    for f in files:
                        archive.write(os.path.join(folder, f["name"]), f["name"])
            order = list(STEM_LABELS)
            files.sort(key=lambda f: order.index(f["name"][:-4]) if f["name"][:-4] in order else 99)
            self.storage.update_job(job_id, status="done", stage="Готово", progress=100, result={
                "files": files, "midi": midi, "engine": "mureka", "pro": True,
                "archives": [n for n in ("stems.zip", "midi.zip") if os.path.isfile(os.path.join(folder, n))]})
        except Exception as error:  # noqa: BLE001 -- любая ошибка -> деньги назад
            self._fail(job_id, f"глубокое разделение не удалось: {error}")

    def _run_voice(self, job_id: str) -> None:
        """Мой голос: вокал из записи (Demucs), без пауз, 30 с -> Mureka
        song/vocal-clone -> vocal_id в список голосов человека."""
        try:
            job = self.storage.job(job_id)
            settings = dict(job.settings or {})
            task_input = settings.get("input") or {}
            folder = self.folder(job_id)
            vocals = self._separate_vocals(job_id, settings)
            self.storage.update_job(job_id, status="running", stage="Учим нейросеть вашему тембру",
                                    progress=60)
            sample = os.path.join(folder, "for_mureka.mp3")
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", vocals, "-af",
                            "silenceremove=stop_periods=-1:stop_duration=0.4:stop_threshold=-38dB",
                            "-t", "30", "-ac", "1", "-b:a", "192k", sample], check=True, timeout=300)
            name = settings.get("voiceName") or "Мой голос"
            vocal_id = mureka_upload_for(task_input, sample, "", "/v1/song/vocal-clone",
                                         {"description": name[:200]}, key="vocal_id")
            self.storage.add_voice(job.user_id, name, vocal_id, settings.get("consent") or {})
            for extra in os.listdir(folder):
                if extra.endswith(".mp3") and extra != "source.mp3":
                    os.remove(os.path.join(folder, extra))
            self.storage.update_job(job_id, status="done", stage="Готово", progress=100,
                                    result={"files": [], "voice": name})
        except Exception as error:  # noqa: BLE001
            self._fail(job_id, f"не получилось запомнить голос: {error}")

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
        if (job.settings or {}).get("mode") == "voice":
            self._run_voice(job_id)
            return
        if (job.settings or {}).get("mode") == "stems":
            self._run_stems_pro(job_id)
            return
        try:
            settings = dict(job.settings or {})
            task_input = settings.get("input") or {}
            mode = settings.get("mode", "restyle")
            folder = self.folder(job_id)
            if mode != "extend":
                self._start_cover(job_id)
            settings = dict(self.storage.job(job_id).settings or {})
            remote = settings.get("remote")
            if not remote:
                self.storage.update_job(job_id, status="running", stage="Отправляем в Mureka",
                                        progress=3)
                try:
                    remote = self._mureka_start(mode, task_input, folder)
                except MurekaNoMoney:
                    if self._to_ace(job_id, mode):
                        return
                    raise RuntimeError("генерация временно недоступна — пополняем счёт "
                                       "нейросети, попробуйте позже") from None
                settings["remote"] = remote
                self.storage.update_job(job_id, settings=settings)
            status = self._mureka_wait(job_id, remote.get("kind", "song"), remote["id"],
                                       job.created_at)
            prefix = {"create": "create", "extend": "extend"}.get(mode, "restyle")
            files = []
            for number, choice in enumerate(status.get("choices") or [], 1):
                if not choice.get("url"):
                    continue
                name = f"{prefix}_{number}.mp3"
                fetch_result(task_input, choice["url"], folder, name)
                if os.path.isfile(os.path.join(folder, name)):
                    # id песни у Mureka -- чтобы потом её продлить (song/extend)
                    files.append({"name": name, "label": label_of(prefix, name),
                                  "seconds": round((choice.get("duration") or 0) / 1000),
                                  "mid": choice.get("id"), "ms": choice.get("duration")})
            if not files:
                raise RuntimeError("Mureka закончила, но версии до сайта не дошли")
            self.storage.update_job(job_id, status="done", stage="Готово", progress=100, result={
                "files": files, "engine": "mureka", "model": status.get("model"),
                "lyrics": task_input.get("lyrics", ""),
                "seconds": (status.get("finished_at") or 0) - (status.get("created_at") or 0)})
        except Exception as error:  # noqa: BLE001 -- любая ошибка -> деньги назад
            self._fail(job_id, str(error))

    def _to_ace(self, job_id: str, mode: str) -> bool:
        """У Mureka кончились деньги: та же задача уходит на ACE-Step, за ту
        же цену и без повторной загрузки. False -- у ACE-Step такого нет
        (продление, «как в образце», голос оригинала), задача падает с
        возвратом денег."""
        job = self.storage.job(job_id)
        settings = dict(job.settings or {})
        task_input = dict(settings.get("input") or {})
        if not ace_fallback() or task_input.get("keep_vocals") or task_input.get("reference"):
            return False
        if mode == "restyle":
            task_input.update(runpod_restyle_recipe(float(task_input.get("audio_influence", 0.5)), 0.0))
        elif mode != "create" or not ace_create_open():
            return False
        # Голос у ACE-Step -- только словами в описании стиля
        voice = VOICES.get(task_input.get("voice", ""), ("", ""))[1]
        task_input["prompt"] = ", ".join(p for p in (task_input.get("prompt", ""), voice) if p)[:1024]
        settings.update(engine="runpod", input=task_input, fellBack="mureka")
        settings.pop("remote", None)
        self.storage.update_job(job_id, settings=settings, status="running", progress=3,
                                stage="Делаем на своём движке")
        self._run(job_id)
        return True

    def _mureka_start(self, mode: str, task_input: dict, folder: str) -> dict:
        voice, lyrics, gender = voice_plan(task_input.get("voice", ""),
                                           task_input.get("lyrics", "")[:5000])
        prompt = ", ".join(p for p in (task_input.get("prompt", ""),
                                       VOICES.get(voice, ("", ""))[1]) if p)[:1024]
        if mode == "extend":
            started = mureka_call("POST", "/v1/song/extend", {
                "song_id": task_input["song_id"], "lyrics": lyrics[:3000],
                "extend_at": int(task_input.get("extend_at") or 0)})
            if not started.get("id"):
                raise RuntimeError(f"Mureka не приняла задачу: {str(started)[:200]}")
            return {"engine": "mureka", "id": started["id"], "kind": "song"}
        if mode == "create":
            if lyrics.strip():
                body = {"lyrics": lyrics, "model": MUREKA_SONG_MODEL, "n": 2}
                # Образец стиля (reference_id) Mureka не сочетает с описанием,
                # а голос (vocal_id) -- сочетает и с тем, и с другим.
                if task_input.get("reference"):
                    source = next(os.path.join(folder, f) for f in sorted(os.listdir(folder))
                                  if f.startswith("source."))
                    body["reference_id"] = mureka_upload_for(
                        task_input, mureka_source(source, folder), "reference")
                else:
                    body["prompt"] = prompt
                if task_input.get("vocal_id"):
                    body["vocal_id"] = task_input["vocal_id"]
                elif gender:
                    body["gender"] = gender
                started = mureka_call("POST", "/v1/song/generate", body)
                kind = "song"
            else:
                started = mureka_call("POST", "/v1/instrumental/generate", {
                    "prompt": task_input.get("prompt", "")[:1024], "model": MUREKA_SONG_MODEL, "n": 2})
                kind = "instrumental"
        else:
            source = next(os.path.join(folder, f) for f in sorted(os.listdir(folder))
                          if f.startswith("source."))
            prepared = mureka_source(source, folder)
            if not lyrics.strip():
                # remix поёт по тексту: не вставили -- распознаём слова сами
                # (song/recognize, $0.01), так советует документация Mureka.
                lyrics = recognize_lyrics(task_input, prepared)
                task_input["lyrics"] = lyrics
            upload_id = mureka_upload_for(task_input, prepared, "remix")
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
        files, midi = [], []
        for item in output.get("files") or []:
            name = safe_file_name(item.get("name", ""))
            if not name or not os.path.isfile(os.path.join(folder, name)):
                continue
            # MIDI от YuE2/SheetSage2 -- не версии трека, а «MIDI партий» (с табами)
            if name.endswith(".mid") and (job.settings or {}).get("engine") == "yue2":
                midi.append({"name": name, "label": MIDI_LABELS.get(name[:-4], label_of(mode, name).replace(
                    "Вариант", "Ноты варианта"))})
            else:
                files.append({"name": name, "label": label_of(mode, name)})
        if not files and not midi:
            raise RuntimeError("Воркер закончил, но файлы до сайта не дошли")
        order = list(STEM_LABELS)
        files.sort(key=lambda f: order.index(f["name"][:-4]) if f["name"][:-4] in order else 99)
        info = output.get("info") or {}
        self.storage.update_job(job_id, status="done", stage="Готово", progress=100, result={
            "files": files,
            # YuE2: слова (присланные или распознанные) и то, что SheetSage2
            # услышал в исходнике, -- аккорды, тональность, части песни
            **({"lyrics": info["lyrics"]} if info.get("lyrics") else {}),
            **({"engine": "yue2", "sheet": {k: info.get(k) for k in ("chords", "key", "structure")},
                "midi": midi, "abc": info.get("abc") or "", "yue2": info.get("settings") or {}}
               if info.get("engine") == "yue2" else {}),
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
