"""Проба YuE2-3B на RunPod Serverless -- только для владельца сайта.

Веса YuE2, SheetSage2 и MERT-v2 -- CC BY-NC 4.0: покупателям не показываем,
пока у NASLUX нет коммерческой лицензии M-A-P.

Режимы (input.mode):
  create     -- песня с нуля: style + lyrics ([verse]/[chorus]);
  cover      -- кавер записи (audio_url): SheetSage2 снимает ноты мелодии,
                слова -- присланные (lyrics) или распознанные Qwen3-ASR по
                частям песни, YuE2 поёт их в новом стиле (cot=melody);
  restyle    -- то же, что cover (так режим называется на сайте);
  transcribe -- только SheetSage2: аккорды, тональность, структура, ноты;
  lyrics     -- только слова записи (Qwen3-ASR), размеченные по частям;
  ping       -- видна ли видеокарта.

variants -- сколько версий (разные seed). Результат: при upload_url файлы
уходят туда POST'ом (как у основного воркера), иначе mp3 в base64 в ответе.
"""
import base64
import gc
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request

import runpod

_cache = {}
SKIP_PARTS = {"silence", "intro", "outro", "interlude", "instrumental", "solo", "fade-out",
              "preshot", "theme", "development", "variation"}
PART_NAMES = {"intro": "Intro", "verse": "Verse", "chorus": "Chorus", "bridge": "Bridge",
              "pre-chorus": "Pre-Chorus", "post-chorus": "Post-Chorus", "outro": "Outro",
              "interlude": "Interlude", "instrumental": "Interlude", "solo": "Interlude", "rap": "Verse",
              "intro and verse": "Verse", "pre-chorus and chorus": "Chorus",
              "verse and pre-chorus": "Verse", "pre-outro": "Bridge"}
# Части песни, как их подписывают на русских сайтах аккордов
RU_PARTS = [(r"пред[- ]?припев", "Pre-Chorus"), (r"куплет", "Verse"), (r"припев", "Chorus"),
            (r"бридж|переход", "Bridge"), (r"вступлен", "Intro"), (r"проигрыш|соло", "Interlude"),
            (r"кода|конец|концовк|аутро", "Outro")]
CHORD = re.compile(r"^[A-H](#|b)?(m(?!aj)|min)?(maj|dim|aug|sus|add)?\d{0,2}(/[A-H](#|b)?)?$")


def pipe():
    if "yue2" not in _cache:
        from yue2 import YuE2Pipeline
        started = time.time()
        _cache["yue2"] = YuE2Pipeline.from_pretrained("/models/YuE2-3B", vae="/models/YuE2-Vae",
                                                      device="cuda", progress=True)
        print(f"[yue2] модель загружена за {time.time() - started:.0f} с", flush=True)
    return _cache["yue2"]


def clean_lyrics(text):
    """Лист с сайта аккордов -> текст для YuE2: строки из одних аккордов
    выкидываются, «Куплет 1:» / «Припев:» становятся [Verse] / [Chorus]."""
    out = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        words = [w for w in re.split(r"[\s|]+", line) if w]
        if words and all(CHORD.match(w) for w in words):
            continue
        low = line.lower().rstrip(":").strip()
        if low and len(low) < 30:
            tag = next((name for pattern, name in RU_PARTS if re.match(pattern, low)), None)
            if tag:
                out.append(f"\n[{tag}]")
                continue
        out.append(line)
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()
    return text if text.startswith("[") else f"[Verse]\n{text}" if text else ""


def to_lines(text, words_per_line=7):
    """Сплошной текст распознавания -> строки песни: по знакам препинания,
    длинные куски -- по нескольку слов."""
    lines = []
    for chunk in re.split(r"(?<=[.!?,;])\s+", (text or "").strip()):
        words = chunk.strip(" ,;").split()
        for i in range(0, len(words), words_per_line):
            lines.append(" ".join(words[i:i + words_per_line]))
    return [line for line in lines if line]


def fetch(url, workdir):
    path = os.path.join(workdir, "source")
    req = urllib.request.Request(url, headers={"User-Agent": "naslux-worker"})
    with urllib.request.urlopen(req, timeout=300) as resp, open(path, "wb") as f:
        while chunk := resp.read(1 << 20):
            f.write(chunk)
    wav = os.path.join(workdir, "source.wav")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", path, "-t", "330", "-ac", "2", "-ar", "44100",
                    "-c:a", "pcm_s16le", wav], check=True)
    return wav


def sheetsage(wav, melody_only):
    """SheetSage2 в своём окружении; процесс выходит -- видеопамять свободна."""
    args = ["/venv-ss/bin/python", "/app/ss_run.py", wav] + (["--melody-only"] if melody_only else [])
    done = subprocess.run(args, capture_output=True, text=True, timeout=1200)
    sys.stderr.write(done.stderr[-4000:])
    if done.returncode != 0:
        # Главное -- последняя строка (RuntimeError из ss_run со всеми попытками)
        last = [line for line in done.stderr.strip().splitlines() if line.strip()][-1:] or [""]
        raise RuntimeError(f"SheetSage2 не справился: {last[0][-1200:]}")
    return json.loads(done.stdout.strip().splitlines()[-1])


def recognize(wav, structure, language):
    """Слова записи по частям песни: каждую часть -- отдельно в Qwen3-ASR,
    тогда текст сразу размечен [Verse]/[Chorus], как его ждёт YuE2."""
    import librosa
    import torch
    from qwen_asr import Qwen3ASRModel

    y, sr = librosa.load(wav, sr=16000, mono=True)
    parts = [(s, e, label) for s, e, label in structure if e - s >= 2] or [(0, len(y) / sr, "verse")]
    model = Qwen3ASRModel.from_pretrained("/models/Qwen3-ASR-1.7B", dtype=torch.bfloat16,
                                          device_map="cuda:0", max_new_tokens=512)
    lyrics = []
    try:
        for start, end, label in parts:
            name = PART_NAMES.get(label.lower(), "Verse")
            if label.lower() in SKIP_PARTS:
                lyrics.append(f"[{name}]\n")
                continue
            piece = y[int(start * sr):int(end * sr)]
            text = model.transcribe(audio=(piece, sr), language=language)[0].text if len(piece) else ""
            lines = to_lines(text)
            lyrics.append(f"[{name}]\n" + "\n".join(lines) + "\n" if lines else f"[{name}]\n")
    finally:
        del model
        gc.collect()
        torch.cuda.empty_cache()
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lyrics)).strip()


def render(style, lyrics, variants, seed, **kwargs):
    import soundfile as sf

    files, plans = [], []
    for n in range(variants):
        song = pipe()(style=style, lyrics=lyrics, seed=seed + n, **kwargs)
        with tempfile.TemporaryDirectory() as work:
            wav = f"{work}/song.wav"
            sf.write(wav, song.audio, song.sample_rate)
            mp3 = subprocess.run(["ffmpeg", "-v", "error", "-i", wav, "-b:a", "256k", "-f", "mp3", "pipe:1"],
                                 capture_output=True, check=True).stdout
        files.append((f"yue2_{n + 1}.mp3", mp3))
        plans.append(getattr(song, "abc", "") or "")
    return files, plans


def deliver(data, files):
    upload = data.get("upload_url")
    if not upload:
        return [{"name": name, "bytes": len(raw), "audio_b64": base64.b64encode(raw).decode()}
                for name, raw in files]
    for name, raw in files:
        req = urllib.request.Request(upload, data=raw, method="POST", headers={
            "Content-Type": "audio/mpeg", "X-File-Name": urllib.parse.quote(name)})
        with urllib.request.urlopen(req, timeout=300) as resp:
            resp.read()
    return [{"name": name, "bytes": len(raw)} for name, raw in files]


def handler(job):
    started = time.time()
    data = job.get("input") or {}
    mode = {"restyle": "cover"}.get(data.get("mode"), data.get("mode") or "create")
    info = {"engine": "yue2", "mode": mode}
    try:
        if mode == "ping":
            import torch
            return {"ok": True, "cuda": torch.cuda.is_available(),
                    "gpu": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None}
        variants = max(1, min(3, int(data.get("variants") or 2)))
        seed = int(data.get("seed") or 831001)
        language = {"ru": "Russian", "en": "English"}.get(data.get("language") or "ru")
        style = (data.get("prompt") or data.get("style") or "").strip()
        lyrics = clean_lyrics(data.get("lyrics") or "")
        cfg = {"cfg_scale": float(data["cfg_scale"])} if data.get("cfg_scale") not in (None, "") else {}
        files = []
        if mode == "create":
            files, plans = render(style, lyrics or "[Verse]\n", variants, seed, cot="full", **cfg)
            info["plan"] = plans[0][:6000]
        else:
            with tempfile.TemporaryDirectory() as work:
                wav = fetch(data["audio_url"], work)
                sheet = sheetsage(wav, melody_only=mode == "cover")
                info.update(chords=sheet["chords"], key=sheet["key"], structure=sheet["structure"],
                            duration=sheet.get("duration"))
                if mode in ("cover", "lyrics") and not lyrics:
                    lyrics = recognize(wav, sheet["structure"], language)
                    info["lyricsRecognized"] = True
                info["lyrics"] = lyrics
                if mode == "cover":
                    if not sheet.get("abc"):
                        raise RuntimeError(f"не получилось снять ноты мелодии: {sheet.get('abc_error')}")
                    info["abc"] = sheet["abc"][:6000]
                    files, _ = render(style, lyrics or "[Verse]\n", variants, seed, abc=sheet["abc"],
                                      cot="melody", **cfg)
                elif mode == "transcribe":
                    info["abc"] = (sheet.get("abc") or "")[:20000]
        return {"ok": True, "mode": mode, "files": deliver(data, files), "info": info,
                "seconds": round(time.time() - started, 1)}
    except Exception as error:  # noqa: BLE001
        print(f"[yue2] {mode}: {error!r}", file=sys.stderr, flush=True)
        return {"ok": False, "mode": mode, "error": str(error)[:1500], "info": info,
                "seconds": round(time.time() - started, 1)}


if __name__ == "__main__":
    runpod.serverless.start({"handler": handler})
