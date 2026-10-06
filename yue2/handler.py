"""Проба YuE2-3B на RunPod Serverless -- только для владельца сайта.

Веса YuE2, SheetSage2 и MERT-v2 -- CC BY-NC 4.0: покупателям не показываем,
пока у NASLUX нет коммерческой лицензии M-A-P.

Режимы (input.mode):
  create     -- песня с нуля: style + lyrics ([verse]/[chorus]);
  cover      -- кавер записи (audio_url): SheetSage2 снимает ноты мелодии,
                слова -- присланные (lyrics) или распознанные Qwen3-ASR по
                частям песни, YuE2 поёт их в новом стиле (cot=melody);
  restyle    -- то же, что cover (так режим называется на сайте);
  transcribe -- только SheetSage2: аккорды, тональность, структура, ноты
                и MIDI частей (notes -- так режим называется на сайте);
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


def sheetsage(wav, melody_only, out=None):
    """SheetSage2 в своём окружении; процесс выходит -- видеопамять свободна.
    out -- папка для MIDI частей (мелодия вокала, инструментов, аккорды)."""
    args = ["/venv-ss/bin/python", "/app/ss_run.py", wav] + (["--melody-only"] if melody_only else []) \
        + (["--out", out] if out else [])
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


def abc_to_midi(abc, name):
    """Ноты плана (ABC) -> MIDI через abc2midi: мелодия и аккорды песни для
    DAW и для наших табов. Не вышло -- просто без MIDI."""
    if not abc:
        return None
    with tempfile.TemporaryDirectory() as work:
        src, out = f"{work}/song.abc", f"{work}/song.mid"
        with open(src, "w", encoding="utf-8") as f:
            f.write(abc)
        done = subprocess.run(["abc2midi", src, "-o", out], capture_output=True, text=True, timeout=60)
        if done.returncode != 0 or not os.path.isfile(out):
            print(f"[yue2] abc2midi: {(done.stderr or done.stdout)[-300:]}", file=sys.stderr, flush=True)
            return None
        with open(out, "rb") as f:
            return (name, f.read())


def sampling(creativity):
    """Смелость 0..1 -> температуры плана нот и исполнения (0.5 -- как у авторов)."""
    c = min(max(float(creativity), 0.0), 1.0)
    return {"abc_sampling": {"temperature": round(0.5 + 0.4 * c, 3)},
            "semantic_sampling": {"temperature": round(0.8 + 0.4 * c, 3)}}


def render(style, lyrics, variants, seed, **kwargs):
    """Версии песни + ноты КАЖДОЙ версии, снятые SheetSage2 с готового звука.
    План нот, который YuE2 пишет до пения, по темпу и ритму расходится с
    тем, что она спела (кавер «Пчеловода»: план 128 BPM, спето 125 -- MIDI и
    метроном не совпадали с песней, 06.10). Снятые с записи ноты идут по её
    времени. Не вышло снять -- MIDI из плана (abc2midi), как запасной."""
    import soundfile as sf

    files, sheets = [], []
    for n in range(variants):
        song = pipe()(style=style, lyrics=lyrics, seed=seed + n, **kwargs)
        plan = getattr(song, "abc", "") or ""
        with tempfile.TemporaryDirectory() as work:
            wav = f"{work}/song.wav"
            sf.write(wav, song.audio, song.sample_rate, subtype="PCM_16")
            mp3 = subprocess.run(["ffmpeg", "-v", "error", "-i", wav, "-b:a", "256k", "-f", "mp3", "pipe:1"],
                                 capture_output=True, check=True).stdout
            files.append((f"yue2_{n + 1}.mp3", mp3))
            sheet = {}
            try:
                sheet = sheetsage(wav, melody_only=False, out=f"{work}/midi")
                ready = next((m for m in ("transcription.mid", "melody.mid") if m in (sheet.get("midis") or [])), None)
                if ready:
                    with open(f"{work}/midi/{ready}", "rb") as f:
                        files.append((f"yue2_{n + 1}.mid", f.read()))
            except Exception as error:  # noqa: BLE001 -- ноты не повод терять песню
                print(f"[yue2] ноты версии {n + 1} не сняты: {error!r}", file=sys.stderr, flush=True)
            if not any(name == f"yue2_{n + 1}.mid" for name, _ in files):
                midi = abc_to_midi(plan, f"yue2_{n + 1}.mid")
                if midi:
                    files.append(midi)
            sheets.append({"abc": sheet.get("abc") or plan, "chords": sheet.get("chords") or [],
                           "key": sheet.get("key") or [], "structure": sheet.get("structure") or []})
    return files, sheets


def deliver(data, files):
    upload = data.get("upload_url")
    if not upload:
        return [{"name": name, "bytes": len(raw), "audio_b64": base64.b64encode(raw).decode()}
                for name, raw in files]
    for name, raw in files:
        kind = "audio/midi" if name.endswith(".mid") else "audio/mpeg"
        req = urllib.request.Request(upload, data=raw, method="POST", headers={
            "Content-Type": kind, "X-File-Name": urllib.parse.quote(name)})
        with urllib.request.urlopen(req, timeout=300) as resp:
            resp.read()
    return [{"name": name, "bytes": len(raw)} for name, raw in files]


def handler(job):
    started = time.time()
    data = job.get("input") or {}
    mode = {"restyle": "cover", "notes": "transcribe"}.get(data.get("mode"), data.get("mode") or "create")
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
        # Настройки: сила стиля (cfg 1.0..1.6), смелость (температуры), близость
        # к оригиналу у кавера: full -- мелодия и аккорды оригинала, melody --
        # только мелодия (гармонию YuE2 строит сама), free -- без нот, свои
        # мелодия и гармония на те же слова
        knobs = sampling(data.get("creativity", 0.5))
        if data.get("cfg_scale") not in (None, ""):
            knobs["cfg_scale"] = min(max(float(data["cfg_scale"]), 0.0), 3.0)
        closeness = data.get("closeness") if data.get("closeness") in ("full", "melody", "free") else "melody"
        info["settings"] = {"closeness": closeness, **{k: v for k, v in knobs.items()}}
        files = []
        if mode == "create":
            files, sheets = render(style, lyrics or "[Verse]\n", variants, seed, cot="full", **knobs)
            info.update(abc=sheets[0]["abc"][:20000], chords=sheets[0]["chords"], key=sheets[0]["key"],
                        structure=sheets[0]["structure"])
        else:
            with tempfile.TemporaryDirectory() as work:
                wav = fetch(data["audio_url"], work)
                sheet = sheetsage(wav, melody_only=mode == "cover" and closeness != "full",
                                  out=f"{work}/midi" if mode == "transcribe" else None)
                info.update(chords=sheet["chords"], key=sheet["key"], structure=sheet["structure"],
                            duration=sheet.get("duration"))
                if mode in ("cover", "lyrics") and not lyrics:
                    lyrics = recognize(wav, sheet["structure"], language)
                    info["lyricsRecognized"] = True
                info["lyrics"] = lyrics
                if mode == "cover":
                    if closeness != "free" and not sheet.get("abc"):
                        raise RuntimeError(f"не получилось снять ноты мелодии: {sheet.get('abc_error')}")
                    score = {} if closeness == "free" else {"abc": sheet["abc"]}
                    files, sheets = render(style, lyrics or "[Verse]\n", variants, seed,
                                           cot="melody" if closeness == "melody" else "full", **score, **knobs)
                    # Ноты и аккорды карточки -- кавера (первой версии), а не оригинала
                    info.update(abc=(sheets[0]["abc"] or sheet.get("abc") or "")[:20000],
                                chords=sheets[0]["chords"] or sheet["chords"], key=sheets[0]["key"] or sheet["key"])
                elif mode == "transcribe":
                    info["abc"] = (sheet.get("abc") or "")[:20000]
                    for name in sheet.get("midis") or []:
                        with open(f"{work}/midi/{name}", "rb") as f:
                            files.append((name, f.read()))
        return {"ok": True, "mode": mode, "files": deliver(data, files), "info": info,
                "seconds": round(time.time() - started, 1)}
    except Exception as error:  # noqa: BLE001
        print(f"[yue2] {mode}: {error!r}", file=sys.stderr, flush=True)
        return {"ok": False, "mode": mode, "error": str(error)[:1500], "info": info,
                "seconds": round(time.time() - started, 1)}


if __name__ == "__main__":
    runpod.serverless.start({"handler": handler})
