"""Сохраняет ли Mureka мелодию исходника: живой замер.

Способы: remix (как сейчас на сайте), song/generate с melody_id (загрузка
purpose=melody: вокал, выделенный Demucs, или MIDI вокала), с описанием
стиля и без. Сходство мелодий: высота вокала (pyin) исходника и результата
по нотам в полутонах, без учёта тональности и темпа (сдвиг на медиану,
выравнивание DTW), доля кадров, где ноты совпали с точностью до полутона.

    MUREKA_API_KEY=... python scripts/melody_test.py source.mp3 --midi vocal.mid --out melody
"""
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from web import studio  # noqa: E402

LYRICS = ("[Verse]\nWe are angels in the night\nFlying high above the light\n"
          "[Chorus]\nWe are angels, we are light\nHold on, hold on tight")
STYLE = "nu metal, heavy distorted guitars, powerful drums"


def vocals(path: str, name: str) -> str:
    subprocess.run([sys.executable, "-m", "demucs", "--two-stems=vocals", "-n", "htdemucs", "-o", "sep",
                    path], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return f"sep/htdemucs/{Path(path).stem}/vocals.wav"


def contour(path: str) -> np.ndarray:
    """Высота голоса в полутонах по кадрам 50 мс, только звучащие кадры."""
    import librosa

    y, sr = librosa.load(path, sr=16000, mono=True, duration=60)
    f0, voiced, _ = librosa.pyin(y, fmin=70, fmax=900, sr=sr, frame_length=1024, hop_length=800)
    notes = 12 * np.log2(f0[voiced] / 440.0)
    return notes - np.median(notes) if len(notes) else notes


def similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Доля совпавших нот (±1 полутон, по модулю октавы) после DTW."""
    import librosa

    if len(a) < 20 or len(b) < 20:
        return 0.0
    cost = np.abs(((a[:, None] - b[None, :]) + 6) % 12 - 6)
    _, path = librosa.sequence.dtw(C=cost, subseq=False)
    return round(float(np.mean([cost[i, j] <= 1.0 for i, j in path])), 3)


def wait(task_id: str, kind: str = "song") -> dict:
    while True:
        time.sleep(5)
        status = studio.mureka_call("GET", f"/v1/{kind}/query/{task_id}")
        if status.get("status") not in studio.MUREKA_STATES:
            return status


def generate(name: str, body: dict, out: str, reference: np.ndarray, report: dict) -> None:
    mark = studio.mureka_call("GET", "/v1/account/billing").get("total_spending") or 0
    try:
        task = studio.mureka_call("POST", "/v1/song/generate", body)
    except Exception as error:  # noqa: BLE001
        report[name] = f"отказ: {error}"
        print(name, "отказ:", error, flush=True)
        return
    status = wait(task["id"])
    if status.get("status") != "succeeded":
        report[name] = f"не удалось: {status.get('failed_reason') or status.get('status')}"
        print(name, report[name], flush=True)
        return
    scores = []
    for number, choice in enumerate(status.get("choices") or [], 1):
        target = os.path.join(out, f"{name}_{number}.mp3")
        studio.download(choice["url"], target)
        scores.append(similarity(reference, contour(vocals(target, name))))
    now = studio.mureka_call("GET", "/v1/account/billing").get("total_spending") or 0
    report[name] = {"similarity": scores, "cost_usd": (now - mark) / 100}
    print(f"{name:28} сходство мелодии {scores}  (${(now - mark) / 100:.2f})", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("audio")
    parser.add_argument("--midi", default="")
    parser.add_argument("--remix", nargs="*", default=[], help="готовые результаты remix для сравнения")
    parser.add_argument("--out", default="melody")
    args = parser.parse_args()
    os.makedirs(args.out, exist_ok=True)
    report = {}
    source_vocals = vocals(args.audio, "source")
    reference = contour(source_vocals)
    print(f"исходник: {len(reference)} звучащих кадров", flush=True)
    # Для справки: сходство вокала с самим собой и с чужой мелодией
    report["self"] = similarity(reference, reference)
    report["shuffled"] = similarity(reference, np.random.default_rng(0).permutation(reference))
    print("с собой:", report["self"], " с перемешанной:", report["shuffled"], flush=True)
    for path in args.remix:
        report[f"remix:{Path(path).name}"] = similarity(reference, contour(vocals(path, "remix")))
        print("remix", path, report[f"remix:{Path(path).name}"], flush=True)

    # melody -- вокал (mp3, до 60 с)
    melody_mp3 = os.path.join(args.out, "melody.mp3")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", source_vocals, "-t", "60", "-ac", "1",
                    "-b:a", "128k", melody_mp3], check=True)
    melody_audio = studio.mureka_upload(melody_mp3, "melody")
    base = {"lyrics": LYRICS, "model": studio.MUREKA_SONG_MODEL, "n": 2}
    generate("melody_vocals", {**base, "melody_id": melody_audio}, args.out, reference, report)
    generate("melody_vocals_prompt", {**base, "melody_id": melody_audio, "prompt": STYLE},
             args.out, reference, report)
    if args.midi:
        melody_midi = studio.mureka_upload(args.midi, "melody")
        generate("melody_midi", {**base, "melody_id": melody_midi}, args.out, reference, report)
    Path(args.out, "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
