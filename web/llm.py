"""
Языковые модели через ИИ-шлюз Beget: OpenAI-совместимый API, оплата в
рублях с баланса Beget (без зарубежной карты, из России отвечает напрямую --
не нужен ретранслятор, как для Mureka).

    BEGET_AI_API_KEY=...            ключ из панели Beget (ИИ-шлюз -> API-ключи)
    NASLUX_LLM_BASE=...             адрес шлюза (по умолчанию https://api-llm.beget.com/v1)
    NASLUX_LLM_MODEL=...            модель (по умолчанию claude-opus-5.5)
    NASLUX_LLM=0                    выключить, тексты снова сочиняет Mureka

Сейчас здесь -- сочинение и продление текстов песен (web/app.py,
/api/studio/lyrics*). Текст -- всегда свой: модели прямо запрещено
воспроизводить чужие песни, даже если человек просит «как у ...».
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request

DEFAULT_BASE = "https://api-llm.beget.com/v1"
DEFAULT_MODEL = "claude-opus-5.5"


class LlmNoMoney(RuntimeError):
    """На балансе Beget кончились деньги (или упёрлись в лимит ключа)."""


def key() -> str:
    return os.environ.get("BEGET_AI_API_KEY", "")


def ready() -> bool:
    return bool(key()) and os.environ.get("NASLUX_LLM", "1") == "1"


def model() -> str:
    return os.environ.get("NASLUX_LLM_MODEL", DEFAULT_MODEL)


def chat(messages: list[dict], max_tokens: int = 1500, temperature: float = 0.9, timeout: int = 90) -> str:
    """Один ответ модели. Ошибки -- RuntimeError с коротким текстом (без ключа)."""
    base = os.environ.get("NASLUX_LLM_BASE", DEFAULT_BASE).rstrip("/")
    request = urllib.request.Request(
        f"{base}/chat/completions", method="POST",
        data=json.dumps({"model": model(), "messages": messages, "max_tokens": max_tokens,
                         "temperature": temperature}).encode(),
        headers={"Authorization": f"Bearer {key()}", "Content-Type": "application/json",
                 "User-Agent": "naslux/1.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as error:
        text = error.read().decode("utf-8", "replace")[:200]
        if error.code in (402, 403) or (error.code == 429 and "balance" in text.lower()):
            raise LlmNoMoney(f"шлюз ответил {error.code}: {text}") from error
        raise RuntimeError(f"шлюз ответил {error.code}: {text}") from error
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise RuntimeError(f"шлюз не ответил: {type(error).__name__}") from error
    try:
        return json.loads(body)["choices"][0]["message"]["content"] or ""
    except (ValueError, KeyError, IndexError, TypeError) as error:
        raise RuntimeError("шлюз прислал непонятный ответ") from error


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
