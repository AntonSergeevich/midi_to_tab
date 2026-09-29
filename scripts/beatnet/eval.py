"""Замер темпа: librosa (как было на сайте) против Beat This в ONNX.

Наборы: GuitarSet (темп в имени файла, 00_BN1-129-Eb_comp_mic.wav),
AAM (доли из разметки, темп -- по медиане промежутков), синтетика с
шаффлом (synth.py). Точность 1 -- темп в пределах 4 %, точность 2 --
с точностью до «вдвое/втрое» (такую ошибку музыкант поправит ÷2 / ×2).
"""

import argparse
import json
import re
import sys
import time
from pathlib import Path

import librosa
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from midi2tab import beatnet  # noqa: E402


def truth_sets(args):
    sets = {}
    if args.guitarset:
        files = sorted(Path(args.guitarset).glob("*_comp_mic.wav"))[:args.limit]
        sets["guitarset"] = [(f, float(re.search(r"-(\d+)-", f.name).group(1))) for f in files]
    if args.aam:
        items = []
        for beats in sorted(Path(args.aam).glob("*.beats"))[:args.limit]:
            times = np.array([float(x) for x in beats.read_text().split()])
            audio = next((p for p in beats.parent.glob(beats.stem + ".*") if p.suffix in (".flac", ".wav", ".mp3")), None)
            if audio is not None and len(times) > 8:
                items.append((audio, 60.0 / float(np.median(np.diff(times)))))
        sets["aam"] = items
    if args.synth:
        sets["synth"] = [(f, float(f.name.split("_")[1])) for f in sorted(Path(args.synth).glob("*.wav"))]
    return sets


def score(estimates):
    ratios = np.array([est / true for est, true in estimates if est])
    ok1 = np.abs(ratios - 1) <= 0.04
    ok2 = np.any([np.abs(ratios / f - 1) <= 0.04 for f in (1, 2, 3, 0.5, 1 / 3)], axis=0)
    return {"acc1": round(float(ok1.mean()), 3), "acc2": round(float(ok2.mean()), 3), "n": len(estimates)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--guitarset")
    parser.add_argument("--aam")
    parser.add_argument("--synth")
    parser.add_argument("--models", nargs="*", default=[])
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--out", default="report.json")
    args = parser.parse_args()

    methods = {"librosa": None, **{Path(m).stem: Path(m) for m in args.models}}
    missing = [str(m) for m in methods.values() if m is not None and not m.is_file()]
    if missing:
        raise SystemExit(f"нет моделей: {missing}")
    report = {}
    for set_name, items in truth_sets(args).items():
        report[set_name] = {}
        for method, model in methods.items():
            if model is not None:
                beatnet.MODEL = model
                beatnet._session.cache_clear()
            estimates, spent, audio_seconds, misses = [], 0.0, 0.0, []
            for path, true in items:
                y, sr = librosa.load(str(path), sr=22050, mono=True)
                start = time.time()
                if model is None:
                    tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
                    est = float(np.atleast_1d(tempo)[0])
                else:
                    found = beatnet.track(y, sr)
                    est = found[0] if found else 0.0
                spent += time.time() - start
                audio_seconds += len(y) / sr
                estimates.append((est, true))
                if not est or abs(est / true - 1) > 0.04:
                    misses.append(f"{path.name}: {est:.1f} вместо {true:.1f}")
            result = score(estimates)
            result["sec_per_min"] = round(spent / max(audio_seconds, 1) * 60, 2)
            result["misses"] = misses[:40]
            report[set_name][method] = result
            print(f"{set_name:10s} {method:18s} точность1 {result['acc1']:.3f}  точность2 {result['acc2']:.3f}  "
                  f"n={result['n']}  {result['sec_per_min']} с на минуту звука", flush=True)
    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
