"""
Свой голос: нейросеть поёт голосом человека (GenAPI, задачи voice_validate
и voice_create, затем generate с persona_id).

Как устроено у провайдера (проба 12.11):
1. voice_validate: образец пения (10-60 с чистого голоса) -> фраза, которую
   человек должен прочитать вслух, и task_id. Бесплатно, ~30 с.
2. voice_create: task_id + запись этой фразы тем же голосом -> voiceId.
   Защита от чужого голоса: фразу читает тот же человек, что пел.
3. generate с persona_id=voiceId, persona_model=voice_persona -- песня
   этим голосом.

Пока это проба владельца (страница /voice). Состояние -- в папке данных
voices/<id пользователя>/state.json, записи -- там же (sample.mp3, phrase.mp3).
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
import time

from . import genapi

PERSONA_MODEL = "voice_persona"
SAMPLE_MIN, SAMPLE_MAX = 10.0, 60.0
_lock = threading.Lock()


def folder(user_id: str) -> str:
    path = os.path.join(os.environ.get("MIDI2TAB_DATA", "data"), "voices", user_id)
    os.makedirs(path, exist_ok=True)
    return path


def load(user_id: str) -> dict:
    try:
        with open(os.path.join(folder(user_id), "state.json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"stage": "none"}


def save(user_id: str, state: dict) -> dict:
    state = {**state, "updated": time.time()}
    path = os.path.join(folder(user_id), "state.json")
    with open(path + ".part", "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)
    os.replace(path + ".part", path)
    return state


def voice_id(user_id: str) -> str:
    """Готовый голос человека, если он есть."""
    state = load(user_id)
    return state.get("voice_id", "") if state.get("stage") == "ready" else ""


def to_mp3(source: str, target: str) -> float:
    """Запись из браузера (webm, m4a, wav...) -> mp3 моно; длительность в секундах."""
    out = subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", source, "-ac", "1", "-ar", "44100",
                          "-b:a", "192k", target], capture_output=True, text=True, timeout=180)
    if out.returncode != 0 or not os.path.isfile(target):
        raise ValueError("Не получилось прочитать запись — попробуйте другой файл или запишите заново")
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                            "-of", "default=nw=1:nk=1", target], capture_output=True, text=True, timeout=60)
    try:
        return float(probe.stdout.strip())
    except ValueError:
        return 0.0


def _found(state: dict, *names: str) -> str:
    """Поле из ответа GenAPI: в result[0], full_response[0] или в корне."""
    places = [x for x in list(state.get("result") or []) + list(state.get("full_response") or [])
              if isinstance(x, dict)] + [state]
    for place in places:
        for name in names:
            if place.get(name) not in (None, ""):
                return str(place[name])
    return ""


def _short(state: dict) -> str:
    return json.dumps({k: state.get(k) for k in ("status", "result", "cost", "runtime")},
                      ensure_ascii=False)[:1500]


def check_sample(user_id: str, link: str, seconds: float) -> None:
    """Шаг 1 в фоне: образец -> фраза для чтения вслух."""
    end = round(min(seconds, SAMPLE_MAX), 1)
    try:
        rid = genapi.start(genapi.SUNO, {"model": genapi.SUNO_MODEL, "task": "voice_validate",
                                         "translate_input": False, "upload_url": link,
                                         "vocal_start_s": 0, "vocal_end_s": end, "language": "ru"})
        state = genapi.wait(rid, timeout=300, every=4)
        phrase, task_id = _found(state, "validate_info", "phrase"), _found(state, "task_id")
        if not phrase or not task_id:
            raise RuntimeError(f"нейросеть не дала фразу: {_short(state)[:300]}")
        save(user_id, {"stage": "phrase", "phrase": phrase, "task_id": task_id,
                       "cost": state.get("cost"), "raw_validate": _short(state)})
    except Exception as error:  # noqa: BLE001 -- любую ошибку показываем человеку
        print(f"Свой голос: образец не принят -- {error}", flush=True)
        save(user_id, {"stage": "error", "step": "sample", "error": genapi.neutral(str(error))[:300]})


def create_voice(user_id: str, link: str) -> None:
    """Шаг 2 в фоне: запись фразы -> voiceId."""
    before = load(user_id)
    try:
        rid = genapi.start(genapi.SUNO, {"model": genapi.SUNO_MODEL, "task": "voice_create",
                                         "translate_input": False, "task_id": before["task_id"],
                                         "upload_url": link})
        state = genapi.wait(rid, timeout=600, every=5)
        made = _found(state, "voice_id", "voiceId", "persona_id", "personaId", "id")
        if not made:
            raise RuntimeError(f"нейросеть не прислала голос: {_short(state)[:300]}")
        save(user_id, {"stage": "ready", "voice_id": made, "phrase": before.get("phrase", ""),
                       "cost": state.get("cost"), "raw_create": _short(state), "created": time.time()})
    except Exception as error:  # noqa: BLE001
        print(f"Свой голос: голос не создан -- {error}", flush=True)
        # фраза остаётся: можно прочитать ещё раз, не начиная сначала
        save(user_id, {**{k: before.get(k) for k in ("phrase", "task_id")},
                       "stage": "error", "step": "phrase", "error": genapi.neutral(str(error))[:300]})


def run_later(target, *args) -> None:
    threading.Thread(target=target, args=args, daemon=True).start()


def public(state: dict) -> dict:
    """Что показать на странице (без сырых ответов)."""
    return {k: state.get(k) for k in ("stage", "phrase", "error", "step", "updated")
            if state.get(k) not in (None, "")} | {"ready": state.get("stage") == "ready"}
