"""
Языковые модели через ИИ-шлюз Beget: OpenAI-совместимый API, оплата в
рублях с баланса Beget (без зарубежной карты, из России отвечает напрямую --
не нужен ретранслятор, как для Mureka).

    BEGET_AI_API_KEY=...            ключ из панели Beget (ИИ-шлюз -> API-ключи)
    NASLUX_LLM_BASE=...             адрес шлюза (по умолчанию https://api-llm.beget.com/v1)
    NASLUX_LLM_MODEL=...            модель (по умолчанию claude-opus-5.5)
    NASLUX_LLM_EFFORT=...           рассуждения модели (по умолчанию low -- без них,
                                    втрое дешевле; пусто -- как решит шлюз)
    NASLUX_LLM=0                    выключить, тексты снова сочиняет Mureka

Сейчас здесь -- сочинение и продление текстов песен (web/app.py,
/api/studio/lyrics*). Текст -- всегда свой: модели прямо запрещено
воспроизводить чужие песни, даже если человек просит «как у ...».
"""
from __future__ import annotations

import collections
import json
import os
import re
import time
import urllib.error
import urllib.request

DEFAULT_BASE = "https://api-llm.beget.com/v1"
DEFAULT_MODEL = "claude-opus-5.5"


# Последние сбои шлюза -- для проверки сайта (/api/health): журналов сервера
# из облака не видно, а причину («не тот ответ», «нет денег», таймаут) надо знать
errors: collections.deque = collections.deque(maxlen=10)


def note_error(where: str, error: str) -> None:
    errors.append({"когда": time.strftime("%Y-%m-%d %H:%M:%S"), "где": where, "что": str(error)[:300]})


class LlmNoMoney(RuntimeError):
    """На балансе Beget кончились деньги (или упёрлись в лимит ключа)."""


KEY_FILE_NAME = "beget_ai_key"


def _key_file() -> str:
    return os.path.join(os.environ.get("MIDI2TAB_DATA", "data"), KEY_FILE_NAME)


def key() -> str:
    """Ключ шлюза: из настроек сервера, а если там его нет или он явно
    испорчен (короче 20 символов -- обрезался при вставке в консоли) -- из
    файла, который присылает проверка сайта из секрета GitHub (save_key)."""
    env = os.environ.get("BEGET_AI_API_KEY", "")
    if len(env.strip().strip("\"'")) >= 20:
        return env
    try:
        with open(_key_file(), encoding="utf-8") as f:
            saved = f.read().strip()
        return saved or env
    except OSError:
        return env


def save_key(value: str) -> None:
    value = value.strip()
    if not (20 <= len(value) <= 300) or not value.isascii() or any(ch.isspace() for ch in value):
        raise ValueError("ключ не похож на ключ шлюза")
    path = _key_file()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w", encoding="utf-8") as f:
        f.write(value)
    os.replace(tmp, path)


def clean_key() -> str:
    """Ключ для запроса: без пробелов, переводов строки и кавычек по краям."""
    return key().strip().strip("\"'").strip()


def key_shape() -> str:
    """Каков ключ -- без самого ключа: длина, пробелы и кавычки по краям
    (частая беда при вставке в файл настроек), невидимые символы."""
    raw = key()
    bad = [name for name, hit in (("пробел/перевод строки по краям", raw != raw.strip()),
                                  ("кавычки", raw[:1] in "\"'" or raw[-1:] in "\"'"),
                                  ("не ASCII", not raw.isascii()),
                                  ("пробел внутри", " " in raw.strip())) if hit]
    return f"{len(raw)} симв., {', '.join(bad) or 'без лишних символов'}"


def ready() -> bool:
    return bool(key()) and os.environ.get("NASLUX_LLM", "1") == "1"


def model() -> str:
    return os.environ.get("NASLUX_LLM_MODEL", DEFAULT_MODEL)


def chat(messages: list[dict], max_tokens: int = 3000, temperature: float = 0.9, timeout: int = 90) -> str:
    """Один ответ модели. Ошибки -- RuntimeError с коротким текстом (без ключа)."""
    base = os.environ.get("NASLUX_LLM_BASE", DEFAULT_BASE).rstrip("/")
    effort = os.environ.get("NASLUX_LLM_EFFORT", "low")
    request = urllib.request.Request(
        f"{base}/chat/completions", method="POST",
        # Claude через шлюз по умолчанию сперва «думает»: замер 28.10 -- 2 200
        # токенов рассуждений на один текст, 36 с и 6,8 ₽ вместо 14 с и 2 ₽,
        # а при малом max_tokens рассуждения съедали весь ответ (пустой текст).
        # Для песни рассуждения не нужны: reasoning_effort=low их отключает
        data=json.dumps({"model": model(), "messages": messages, "max_tokens": max_tokens,
                         "temperature": temperature,
                         **({"reasoning_effort": effort} if effort else {})}).encode(),
        headers={"Authorization": f"Bearer {clean_key()}", "Content-Type": "application/json",
                 "User-Agent": "naslux/1.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as error:
        text = error.read().decode("utf-8", "replace")[:200]
        note_error("шлюз", f"HTTP {error.code}: {text} | сервер {error.headers.get('Server', '?')}, "
                           f"{error.headers.get('Content-Type', '?')} | ключ: {key_shape()}")
        if error.code in (402, 403) or (error.code == 429 and "balance" in text.lower()):
            raise LlmNoMoney(f"шлюз ответил {error.code}: {text}") from error
        raise RuntimeError(f"шлюз ответил {error.code}: {text}") from error
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        note_error("шлюз", f"не ответил: {type(error).__name__}: {error}")
        raise RuntimeError(f"шлюз не ответил: {type(error).__name__}") from error
    try:
        data = json.loads(body)
        choice = data["choices"][0]
        text = choice["message"].get("content") or ""
    except (ValueError, KeyError, IndexError, TypeError) as error:
        raise RuntimeError("шлюз прислал непонятный ответ") from error
    usage = data.get("usage") or {}
    print(f"ИИ-шлюз: {model()}, {usage.get('completion_tokens')} ток., {usage.get('cost_rub')} ₽, "
          f"{choice.get('finish_reason')}", flush=True)
    if not text.strip() and choice.get("finish_reason") == "length":
        raise RuntimeError("ответ не уместился в лимит токенов")
    return text


# ------------------------------------------------------------------ тексты

RULES = """Ты -- автор текстов песен. Пишешь для музыкального сервиса NASLUX: по твоему
тексту нейросеть споёт песню.

Правила:
- Текст только свой, новый. Никогда не воспроизводи и не пересказывай строки
  существующих песен, даже если просят «как у ...» -- тогда пиши своё в том же
  настроении и манере, без цитат.
- Язык -- как в просьбе (по умолчанию русский). Живой разговорный язык, сленг --
  если просят или он к месту.
- Песню удобно петь: ровный размер внутри части, строки примерно одной длины,
  точные рифмы, яркие образы, без канцелярита и штампов.
- Структура пометками на отдельных строках, по-английски в квадратных скобках:
  [Verse], [Chorus], [Bridge], [Outro] (при желании [Intro], [Pre-Chorus]).
- Обычно: два куплета по 4-8 строк, припев 4-6 строк (повторяется после каждого
  куплета), бридж, финальный припев. 16-40 строк всего.
- Без аккордов, ремарок, пояснений и эмодзи -- только то, что поётся.
- Без оскорблений, призывов к насилию и того, что нарушает закон."""


def _json_answer(text: str) -> dict:
    """Ответ модели -> dict: ищем JSON в тексте (модель иногда обрамляет его)."""
    found = re.search(r"\{.*\}", text or "", re.S)
    if found:
        try:
            value = json.loads(found.group(0), strict=False)    # переводы строк прямо в строке JSON
            if isinstance(value, dict):
                return value
        except ValueError:
            pass
    return {}


def _clean(lyrics: str) -> str:
    lines = [line.rstrip() for line in (lyrics or "").replace("\r", "").split("\n")]
    out = "\n".join(lines).strip()
    return re.sub(r"\n{3,}", "\n\n", out)[:3000]


def write_lyrics(prompt: str) -> dict:
    """Текст песни по описанию: {"title", "lyrics"}."""
    answer = chat([
        {"role": "system", "content": RULES},
        {"role": "user", "content": (
            "Напиши песню. О чём и какая она:\n" + prompt.strip()[:1500]
            + '\n\nОтветь одним JSON без пояснений: {"title": "название", "lyrics": "текст с пометками"}')},
    ])
    data = _json_answer(answer)
    lyrics = _clean(str(data.get("lyrics") or ""))
    title = str(data.get("title") or "").strip()[:80]
    if not lyrics:
        # без JSON: весь ответ -- текст, первая короткая строка без пометки -- название
        lines = _clean(answer).split("\n")
        if lines and lines[0] and not lines[0].startswith("[") and len(lines[0]) < 60:
            title, lines = lines[0].strip("«»\"' #*"), lines[1:]
        lyrics = _clean("\n".join(lines))
    if not lyrics:
        raise RuntimeError("модель не прислала текст")
    return {"title": title, "lyrics": lyrics}


def extend_lyrics(lyrics: str) -> dict:
    """Продолжение песни (только новая часть, как у Mureka lyrics/extend): {"lyrics"}."""
    answer = chat([
        {"role": "system", "content": RULES},
        {"role": "user", "content": (
            "Вот песня. Допиши ПРОДОЛЖЕНИЕ -- то, что прозвучит после неё: новый куплет и "
            "припев (или бридж и финальный припев). Тот же размер, рифмовка, герой и тема; "
            "уже написанное не повторяй, кроме припева.\n\n" + lyrics.strip()[:3000]
            + '\n\nОтветь одним JSON без пояснений: {"lyrics": "продолжение с пометками"}')},
    ])
    data = _json_answer(answer)
    out = _clean(str(data.get("lyrics") or "")) or _clean(answer if not _json_answer(answer) else "")
    if not out:
        raise RuntimeError("модель не прислала текст")
    return {"lyrics": out}


# ------------------------------------------------------------------- стиль

STYLE_RULES = """Ты -- музыкальный продюсер. Человек описал стиль будущей песни своими словами.
Сделай из этого точное и красивое описание стиля для нейросети, которая сочиняет музыку.

Правила:
- Сохрани всё, что человек написал: жанр, настроение, инструменты, голос, темп,
  отсылки. Ничего не меняй по смыслу -- только уточняй и дополняй.
- Допиши недостающее: поджанр, темп (BPM), характер вокала (мужской/женский,
  подача), ключевые инструменты и звук (продакшн), настроение, развитие песни
  (например, тихий куплет и мощный припев).
- Имена исполнителей и названия песен не вставляй: если человек пишет «как у ...»,
  опиши сам звук -- жанр, эпоху, инструменты, подачу.
- style -- по-русски, короткими фразами через запятую, 120-350 символов, без кавычек
  и пояснений.
- tags -- то же по-английски тегами для музыкальной нейросети через запятую
  (genre, subgenre, mood, vocal type, instruments, tempo bpm, production),
  до 300 символов."""


def _field(answer: str, name: str) -> str:
    """Поле из ответа модели: JSON; не разобрался (кавычки внутри текста) --
    вытащить значение по имени поля."""
    data = _json_answer(answer)
    if data.get(name):
        return str(data[name])
    found = re.search(r'"%s"\s*:\s*"(.*?)"\s*(?:,\s*"[a-z]+"\s*:|\}\s*$)' % name, answer or "", re.S)
    return found.group(1) if found else ""


def write_style(style: str, lyrics: str = "") -> dict:
    """Стиль человека -> {"style": по-русски для поля, "tags": по-английски для YuE2}."""
    hint = f"\n\nТекст песни (для настроения, не пересказывай):\n{lyrics.strip()[:1200]}" if lyrics.strip() else ""
    ask = [
        {"role": "system", "content": STYLE_RULES},
        {"role": "user", "content": "Стиль от человека:\n" + style.strip()[:500] + hint
         + '\n\nОтветь одним JSON без пояснений, без кавычек внутри значений: {"style": "...", "tags": "..."}'},
    ]
    answer = ""
    for attempt in range(2):                 # непонятный ответ -- ещё одна попытка
        answer = chat(ask, max_tokens=1200, temperature=0.7)
        out = re.sub(r"\s+", " ", _field(answer, "style")).strip(" \"«»")[:500]
        if not out and answer.strip() and not answer.strip().startswith("{"):
            out = re.sub(r"\s+", " ", answer).strip(" \"«»")[:500]     # ответил просто текстом
        if out:
            tags = re.sub(r"\s+", " ", _field(answer, "tags")).strip(" \"")[:500]
            if re.search(r"[А-Яа-яЁё]", tags):
                tags = ""              # теги -- только латиница, иначе YuE2 их не поймёт
            return {"style": out, "tags": tags}
        note_error("стиль", f"непонятный ответ (попытка {attempt + 1}): {answer[:200]}")
    raise RuntimeError("модель не прислала стиль")


# ----------------------------------------------------------------- обложки

COVER_RULES = """You are an art director for album covers. From a song's title, lyrics and style write ONE
image prompt in English for an image model (SDXL), 35-70 words, a single paragraph.

Rules:
- Pick a clear visual idea from the song's meaning and mood: a concrete subject and scene,
  not a list of words from the lyrics.
- Name the art style that fits the genre (for example: moody film photograph, oil painting,
  bold minimal vector poster, retro 80s synthwave, ink illustration, surreal collage) and
  vary it between songs.
- Describe color palette, lighting and composition (square album cover, strong focal point).
- Never ask for text, letters, words, logos or watermarks. No real people, celebrities or
  existing characters. Nothing violent, sexual or hateful.
- Do not use dashes or quotes. Reply with the prompt only."""


def cover_prompt(title: str, lyrics: str, style: str) -> str:
    """Описание обложки для SDXL -- по смыслу песни, а не машинным переводом строк."""
    answer = chat([
        {"role": "system", "content": COVER_RULES},
        {"role": "user", "content": f"Title: {title.strip()[:120]}\nStyle: {style.strip()[:300]}\n"
                                    f"Lyrics:\n{lyrics.strip()[:1500]}"},
    ], max_tokens=1200, temperature=0.9)
    text = re.sub(r"\s+", " ", answer or "").strip().strip("\"'«»")
    text = re.sub(r"\s[-–—]\s", ", ", text)          # без тире: обработчик режет «Исполнитель - Песня»
    if len(text) < 20 or re.search(r"[А-Яа-яЁё]", text):
        raise RuntimeError("модель не прислала описание обложки")
    return text[:600]
