"""Обработчик задач GPU-воркера NASLUX на RunPod Serverless.

Режимы (поле input.mode):
  restyle  -- переделать трек в другой стиль (ACE-Step cover, модель turbo);
              strength: 0..1, чем меньше -- тем дальше от оригинала;
  enrich   -- дописать партию поверх трека (ACE-Step lego, модель base),
              track: drums / bass / guitar / keyboard / strings / ...;
  extract  -- вытащить одну партию силами ACE-Step (extract, base);
  stems    -- разделить на 6 партий (Demucs htdemucs_6s): вокал, гитара,
              бас, барабаны, клавиши, остальное;
  ping     -- проверить воркер: какая видеокарта, какие модели загружены.

Исходник: input.audio_url (воркер скачивает сам) или input.audio_b64.
Результат: если задан input.upload_url -- каждый файл уходит туда POST'ом
(заголовок X-File-Name), в ответе только имена; иначе mp3 в base64 прямо
в ответе (годится для коротких тестов: у RunPod лимит на размер ответа).

ACE-Step работает своим HTTP-сервером внутри контейнера: сервер живёт всё
время жизни воркера, модели грузятся один раз на холодном старте.
"""
import base64
import glob
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request

import runpod

PORT = int(os.environ.get("ACESTEP_API_PORT", "8901"))
BASE = f"http://127.0.0.1:{PORT}"
# SFT -- модель для переделки (её советуют авторы ACE-Step для Remix),
# base -- для дописывания и вытаскивания партий (lego/complete/extract).
# turbo лежит в образе (без него сервер считает комплект неполным и
# качает всё заново), но в видеопамять не грузится.
SFT, FULL = "acestep-v15-sft", "acestep-v15-base"
TRACKS = {"woodwinds", "brass", "fx", "synth", "strings", "percussion",
          "keyboard", "guitar", "bass", "drums", "backing_vocals", "vocals"}
MAX_SECONDS = 600
_server = {"proc": None}


def _request(path, body=None, timeout=60):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method="POST" if data else "GET",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    return raw if path.startswith("/v1/audio") else json.loads(raw or b"{}")


def start_server():
    if _server["proc"] and _server["proc"].poll() is None:
        return
    _server["proc"] = subprocess.Popen(
        [sys.executable, "-m", "acestep.api_server", "--host", "127.0.0.1", "--port", str(PORT)],
        cwd="/app/ace", stdout=sys.stderr, stderr=subprocess.STDOUT)
    started = time.time()
    while time.time() - started < 900:
        if _server["proc"].poll() is not None:
            raise RuntimeError("сервер ACE-Step упал при запуске, см. логи воркера")
        try:
            _request("/health", timeout=3)
            print(f"[worker] ACE-Step готов за {time.time() - started:.0f} с", flush=True)
            return
        except Exception:
            time.sleep(2)
    raise RuntimeError("сервер ACE-Step не поднялся за 15 минут")


def loaded_models():
    try:
        return _request("/v1/models", timeout=10).get("data")
    except Exception as error:
        return f"не узнать: {error}"


def duration_of(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "csv=p=0", path], capture_output=True, text=True, check=True)
    return float(out.stdout.strip())


def fetch_source(job_input, workdir):
    path = os.path.join(workdir, "source")
    if job_input.get("audio_url"):
        req = urllib.request.Request(job_input["audio_url"], headers={"User-Agent": "naslux-worker"})
        with urllib.request.urlopen(req, timeout=300) as resp, open(path, "wb") as f:
            f.write(resp.read())
    elif job_input.get("audio_b64"):
        with open(path, "wb") as f:
            f.write(base64.b64decode(job_input["audio_b64"].split(",")[-1]))
    else:
        raise ValueError("нужен audio_url или audio_b64")
    # Всё приводим к wav 48 кГц стерео и режем по лимиту: так и ACE-Step, и
    # Demucs получают одно и то же, что бы ни загрузил человек.
    seconds = min(float(job_input.get("seconds") or MAX_SECONDS), MAX_SECONDS)
    wav = os.path.join(workdir, "source.wav")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", path, "-t", str(seconds),
                    "-ac", "2", "-ar", "48000", wav], check=True)
    return wav


def ace_step(body):
    """Отдать задачу серверу ACE-Step, дождаться, вернуть ([mp3...], сведения)."""
    start_server()
    task = _request("/release_task", body, timeout=120)
    task_id = (task.get("data") or {}).get("task_id")
    if not task_id:
        raise RuntimeError(f"ACE-Step не принял задачу: {str(task)[:500]}")
    while True:
        time.sleep(3)
        items = _request("/query_result", {"task_id_list": [task_id]}).get("data") or []
        if not items or items[0].get("status") == 0:
            continue
        result = json.loads(items[0].get("result") or "[]")
        if items[0].get("status") != 1 or not result:
            raise RuntimeError(f"ACE-Step не справился: {json.dumps(result, ensure_ascii=False)[:900]}")
        info = result[0]
        # Сервер молча подставляет основную модель, если запрошенная не
        # загружена, -- и вместо дописывания получилась бы переделка.
        if body.get("model") and info.get("dit_model") and info["dit_model"] != body["model"]:
            raise RuntimeError(f"сервер взял {info['dit_model']} вместо {body['model']}")
        audios = [to_mp3(_request(item["file"], timeout=300)) for item in result if item.get("file")]
        return audios, {k: info.get(k) for k in ("dit_model", "seed_value", "metas")}


def to_mp3(wav: bytes, bitrate: str = "320k") -> bytes:
    return subprocess.run(["ffmpeg", "-v", "error", "-i", "pipe:0", "-b:a", bitrate,
                           "-f", "mp3", "pipe:1"], input=wav, capture_output=True,
                          check=True).stdout


def common(job_input, source):
    return {
        "src_audio_path": source, "audio_duration": duration_of(source),
        "prompt": job_input.get("prompt") or "", "lyrics": job_input.get("lyrics") or "",
        "vocal_language": job_input.get("language") or "ru",
        "thinking": False, "use_cot_caption": False, "use_cot_language": False,
        # wav, а не mp3: своё mp3 у ACE-Step ~128 кбит/с -- для музыки мало,
        # кодируем сами в 320 (to_mp3).
        "batch_size": 1, "audio_format": "wav",
        "use_random_seed": job_input.get("seed") in (None, -1, ""),
        "seed": job_input.get("seed") if job_input.get("seed") not in (None, "") else -1,
    }


def knob(job_input, name, default):
    try:
        return min(max(float(job_input.get(name, default)), 0.0), 1.0)
    except (TypeError, ValueError):
        return default


KEY_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
MAJOR = [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88]
MINOR = [6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17]


def tempo_and_key(path):
    """Темп и тональность исходника. Без них модель получает «N/A» и
    ведёт гармонию и ритм сама -- отсюда каша вместо песни."""
    import librosa
    import numpy as np

    y, sr = librosa.load(path, sr=22050, mono=True, duration=240)
    tempo = float(np.atleast_1d(librosa.beat.beat_track(y=y, sr=sr)[0])[0])
    harmonic = librosa.effects.harmonic(y)
    profile = librosa.feature.chroma_cqt(y=harmonic, sr=sr).mean(axis=1)
    best = max(((float(np.corrcoef(profile, np.roll(w, k))[0, 1]), k, name)
                for k in range(12) for w, name in ((MAJOR, "major"), (MINOR, "minor"))))
    return int(round(tempo)), f"{KEY_NAMES[best[1]]} {best[2]}"


def restyle(job_input, source):
    """Переделка в стиль по рецепту авторов ACE-Step для Remix: модель SFT,
    сила следования исходнику около 0.5, «удержание мелодии» 0.1-0.25,
    темп и тональность исходника. Три крутилки (0..1), как в Suno:

    audio_influence -- влияние загруженной песни: audio_cover_strength
                       0.1..1 и удержание мелодии 0.1..0.25 вместе с ним;
    style_influence -- влияние стиля: guidance 5..9 (7 -- умолчание модели);
    weirdness       -- странность: shift 2.5..4, с 0.75 -- sde-сэмплер.

    Шкалы нарочно узкие: за их пределами гармония разваливается -- так
    и вышло на первой версии, где guidance доходил до 12, а удержания
    мелодии не было вовсе. variants -- сколько версий сделать (1..4):
    авторы советуют выбирать из нескольких, а не ждать одной удачной.
    """
    audio = knob(job_input, "audio_influence", knob(job_input, "strength", 0.5))
    style = knob(job_input, "style_influence", 0.5)
    weird = knob(job_input, "weirdness", 0.3)
    bpm, key = tempo_and_key(source)
    body = {**common(job_input, source), "task_type": "cover",
            "model": FULL if job_input.get("engine") == "base" else SFT,
            "audio_cover_strength": round(0.1 + 0.9 * audio, 3),
            "cover_noise_strength": round(0.1 + 0.15 * audio, 3),
            "guidance_scale": round(5 + 4 * style, 2), "shift": round(2.5 + 1.5 * weird, 2),
            "infer_method": "sde" if weird >= 0.75 else "ode",
            "inference_steps": int(job_input.get("steps") or 50),
            "bpm": bpm, "key_scale": key, "time_signature": "4",
            "batch_size": max(1, min(4, int(job_input.get("variants") or 2)))}
    # Для подбора настроек из тестов: любые поля ACE-Step поверх рецепта.
    body.update(job_input.get("raw") or {})
    audios, info = ace_step(body)
    info["settings"] = {k: body.get(k) for k in (
        "model", "audio_cover_strength", "cover_noise_strength", "guidance_scale", "shift",
        "infer_method", "inference_steps", "bpm", "key_scale", "batch_size")}
    return [(f"restyle_{i + 1}.mp3", a) for i, a in enumerate(audios)], info


def enrich(job_input, source, task_type):
    track = (job_input.get("track") or "").lower()
    if track not in TRACKS:
        raise ValueError(f"track должен быть одним из: {', '.join(sorted(TRACKS))}")
    prompt = job_input.get("prompt") or ""
    body = {**common(job_input, source), "task_type": task_type, "model": FULL,
            "track_name": track, "track_classes": [track], "global_caption": prompt,
            "inference_steps": int(job_input.get("steps") or 32),
            "guidance_scale": float(job_input.get("guidance") or 7.0)}
    audios, info = ace_step(body)
    return [(f"{task_type}_{track}.mp3", audios[0])], info


def stems(job_input, source, workdir):
    out = os.path.join(workdir, "stems")
    subprocess.run([sys.executable, "-m", "demucs", "-n", "htdemucs_6s", "-d", "cuda",
                    "--mp3", "--mp3-bitrate", str(job_input.get("bitrate") or 256),
                    "-o", out, source], check=True, stdout=sys.stderr, stderr=subprocess.STDOUT)
    files = []
    for path in sorted(glob.glob(os.path.join(out, "htdemucs_6s", "*", "*.mp3"))):
        with open(path, "rb") as f:
            files.append((os.path.basename(path), f.read()))
    if not files:
        raise RuntimeError("Demucs не выдал ни одной дорожки")
    return files, {"model": "htdemucs_6s"}


def deliver(job_input, files):
    upload = job_input.get("upload_url")
    if not upload:
        return [{"name": name, "bytes": len(data), "audio_b64": base64.b64encode(data).decode()}
                for name, data in files]
    delivered = []
    for name, data in files:
        req = urllib.request.Request(upload, data=data, method="POST", headers={
            "Content-Type": "audio/mpeg", "X-File-Name": urllib.parse.quote(name)})
        with urllib.request.urlopen(req, timeout=300) as resp:
            resp.read()
        delivered.append({"name": name, "bytes": len(data)})
    return delivered


def diagnostics(job_input):
    import torch
    info = {"cuda": torch.cuda.is_available(),
            "gpu": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None}
    if torch.cuda.is_available():
        info["vram_gb"] = round(torch.cuda.get_device_properties(0).total_memory / 1e9, 1)
    if not job_input.get("light"):
        start_server()
        info["models"] = loaded_models()
    try:
        with open("/app/ACESTEP_COMMIT") as f:
            info["acestep_commit"] = f.read().strip()
    except OSError:
        pass
    return info


def handler(job):
    started = time.time()
    job_input = job.get("input") or {}
    mode = job_input.get("mode") or "restyle"
    try:
        if mode == "ping":
            return {"ok": True, **diagnostics(job_input), "seconds": round(time.time() - started, 1)}
        with tempfile.TemporaryDirectory() as workdir:
            source = fetch_source(job_input, workdir)
            if mode == "restyle":
                files, info = restyle(job_input, source)
            elif mode in ("enrich", "extract", "complete"):
                files, info = enrich(job_input, source, "lego" if mode == "enrich" else mode)
            elif mode == "stems":
                files, info = stems(job_input, source, workdir)
            else:
                raise ValueError(f"неизвестный режим {mode!r}")
            delivered = deliver(job_input, files)
        return {"ok": True, "mode": mode, "files": delivered, "info": info,
                "seconds": round(time.time() - started, 1)}
    except Exception as error:
        print(f"[worker] {mode}: {error!r}", file=sys.stderr, flush=True)
        return {"ok": False, "mode": mode, "error": str(error)[:1500],
                "seconds": round(time.time() - started, 1)}


if __name__ == "__main__":
    runpod.serverless.start({"handler": handler})
