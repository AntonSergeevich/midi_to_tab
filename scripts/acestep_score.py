"""Оценка замера (scripts/acestep_eval.py): у каждой песни --
  * разборчивость: Whisper распознаёт спетое, считаем долю ошибок в словах
    (WER) против нашего текста; меньше -- лучше. Мерим на полном миксе:
    числа завышены музыкой, но варианты сравниваются честно -- условия одни;
  * ровность такта: доли нашей моделью Beat This, разброс промежутков между
    долями (CV) и доля «сбоев» -- промежутков, отличающихся от обычного
    больше чем на 12 %; это то, что на слух -- «сбился такт»;
  * время работы видеокарты на песню.
Итог -- report.md и scores.json в папке результатов.

    python scripts/acestep_score.py results scripts/acestep_job_ru.json
"""
import json
import os
import re
import sys
from collections import defaultdict

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))


def words(text: str) -> list[str]:
    text = re.sub(r"\[[^\]]*\]", " ", text.lower().replace("ё", "е"))
    return re.findall(r"[a-zа-я0-9]+", text)


def wer(ref: list[str], hyp: list[str]) -> float:
    d = np.arange(len(hyp) + 1)
    for i, r in enumerate(ref, 1):
        prev, d[0] = d.copy(), i
        for j, h in enumerate(hyp, 1):
            d[j] = min(prev[j] + 1, d[j - 1] + 1, prev[j - 1] + (r != h))
    return float(d[len(hyp)]) / max(1, len(ref))


def rhythm(path: str) -> dict:
    import librosa

    from midi2tab import beatnet

    y, sr = librosa.load(path, sr=beatnet.SR, mono=True)
    found = beatnet.track(y, sr)
    if not found:
        return {"bpm": None, "cv": None, "breaks": None}
    bpm, beats, _ = found
    gaps = np.diff(np.array(beats))
    gaps = gaps[(gaps > 0.15) & (gaps < 2.0)]
    if len(gaps) < 4:
        return {"bpm": round(bpm, 1), "cv": None, "breaks": None}
    step = float(np.median(gaps))
    return {"bpm": round(bpm, 1), "cv": round(float(np.std(gaps) / np.mean(gaps)), 3),
            "breaks": round(float(np.mean(np.abs(gaps - step) > 0.12 * step)), 3)}


def main() -> None:
    folder, job_path = sys.argv[1], sys.argv[2]
    with open(job_path, encoding="utf-8") as f:
        job = json.load(f)
    lyrics = {s["id"]: s["lyrics"] for s in job["songs"]}
    bpms = {s["id"]: s["bpm"] for s in job["songs"]}
    from faster_whisper import WhisperModel

    model = WhisperModel(os.environ.get("WHISPER_MODEL", "medium"), device="cpu", compute_type="int8")
    rows = []
    for name in sorted(os.listdir(folder)):
        if not name.endswith(".mp3"):
            continue
        variant, song = name[:-4].split("__")
        meta = {}
        if os.path.exists(os.path.join(folder, name[:-4] + ".json")):
            with open(os.path.join(folder, name[:-4] + ".json"), encoding="utf-8") as f:
                meta = json.load(f)
        path = os.path.join(folder, name)
        segments, _ = model.transcribe(path, language="ru", beam_size=5, vad_filter=False)
        heard = " ".join(s.text for s in segments)
        row = {"variant": variant, "song": song, "title": meta.get("title", variant),
               "wer": round(wer(words(lyrics[song]), words(heard)), 3),
               "gpu_seconds": meta.get("seconds_worked"), "asked_bpm": bpms[song], **rhythm(path)}
        rows.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
    with open(os.path.join(folder, "scores.json"), "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=1)
    by = defaultdict(list)
    for r in rows:
        by[r["variant"]].append(r)
    mean = lambda xs: round(float(np.mean(xs)), 3) if xs else None   # noqa: E731
    lines = ["# Замер: русский вокал", "",
             "| Вариант | Ошибки в словах (WER) ↓ | Сбои такта ↓ | Разброс долей (CV) ↓ | Видеокарта, с/песня | Песен |",
             "|---|---|---|---|---|---|"]
    for variant, items in sorted(by.items(), key=lambda kv: mean([r["wer"] for r in kv[1]])):
        lines.append(f"| {items[0]['title']} | {mean([r['wer'] for r in items])} "
                     f"| {mean([r['breaks'] for r in items if r['breaks'] is not None])} "
                     f"| {mean([r['cv'] for r in items if r['cv'] is not None])} "
                     f"| {mean([r['gpu_seconds'] for r in items if r['gpu_seconds']])} | {len(items)} |")
    lines += ["", "По песням (WER / сбои такта / темп: вышел — просили):", ""]
    for r in sorted(rows, key=lambda r: (r["song"], r["variant"])):
        lines.append(f"- {r['song']} · {r['title']}: WER {r['wer']}, сбои {r['breaks']}, темп {r['bpm']} — {r['asked_bpm']}")
    with open(os.path.join(folder, "report.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
