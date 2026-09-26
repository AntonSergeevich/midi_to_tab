"""Точность распознавания аккордов NASLUX на размеченных записях.

Эталон -- GuitarSet (Zenodo 3371780, лицензия CC BY 4.0): 360 записей живой
гитары с точной разметкой аккордов; берутся аккомпанементы (*_comp), где
аккорды и играются. Считается по методике MIREX (mir_eval.chord):
  root    -- угадан основной тон;
  majmin  -- угадан мажор/минор с основным тоном (главная метрика);
  sevenths-- с септаккордами;
доля времени, где ответ верный, взвешенная по длительности.

    python scripts/chord_benchmark.py --audio audio_mono-mic --annotations annotation \\
        --limit 60 --out results/chords.csv
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import mir_eval  # noqa: E402

from midi2tab import audiochords  # noqa: E402

# Подписи NASLUX (midi2tab.chords.TEMPLATES) -> качество по Харту для mir_eval
HARTE = {
    "": "maj", "m": "min", "5": "5", "7": "7", "m7": "min7", "maj7": "maj7",
    "6": "maj6", "m6": "min6", "sus4": "sus4", "sus2": "sus2", "dim": "dim",
    "m7b5": "hdim7", "dim7": "dim7", "aug": "aug", "add9": "maj(9)", "9": "9",
    "m9": "min9",
}
METRICS = ("root", "majmin", "sevenths")


def to_harte(name: str) -> str:
    """«F#m7/A» -> «F#:min7». Бас отбрасываем: метрики ниже его не учитывают."""
    if not name or name in ("N", "N.C."):
        return "N"
    name = name.split("/")[0]
    root = name[:2] if len(name) > 1 and name[1] in "#b" else name[:1]
    quality = HARTE.get(name[len(root):])
    return f"{root}:{quality}" if quality else f"{root}:maj"


def reference(jams_path: Path):
    """Аккорды из лид-шита: первая аннотация chord в JAMS GuitarSet."""
    data = json.loads(jams_path.read_text())
    chords = [a for a in data["annotations"] if a["namespace"] == "chord"][0]["data"]
    intervals = [[c["time"], c["time"] + c["duration"]] for c in chords]
    return intervals, [c["value"] for c in chords]


def evaluate(audio: Path, jams_path: Path) -> dict:
    ref_intervals, ref_labels = reference(jams_path)
    analysis = audiochords.detect_from_audio(str(audio))
    est = [c for c in analysis.chords if c.end > c.start]
    est_intervals = [[c.start, c.end] for c in est] or [[0.0, ref_intervals[-1][1]]]
    est_labels = [to_harte(c.name) for c in est] or ["N"]
    import numpy as np

    scores = mir_eval.chord.evaluate(np.array(ref_intervals), ref_labels,
                                     np.array(est_intervals), est_labels)
    return {m: float(scores[m]) for m in METRICS} | {
        "duration": ref_intervals[-1][1],
        "reference": " ".join(ref_labels[:8]),
        "detected": " ".join(c.name for c in est[:8]),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audio", required=True, help="папка с *_comp_mic.wav")
    parser.add_argument("--annotations", required=True, help="папка с *.jams")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--out", default="chords.csv")
    args = parser.parse_args()

    files = sorted(Path(args.audio).glob("*_comp_mic.wav"))
    if args.limit:
        # Берём поровну из каждого стиля: bossa, funk, jazz, rock, singer-songwriter.
        by_style = defaultdict(list)
        for f in files:
            by_style[f.name.split("_")[1][:2]].append(f)
        per_style = max(1, args.limit // max(1, len(by_style)))
        files = [f for group in by_style.values() for f in group[:per_style]]

    rows, started = [], time.time()
    for number, audio in enumerate(files, 1):
        jams_path = Path(args.annotations) / audio.name.replace("_mic.wav", ".jams")
        try:
            row = {"file": audio.name, "style": audio.name.split("_")[1][:2],
                   **evaluate(audio, jams_path)}
        except Exception as error:  # noqa: BLE001 -- одна плохая запись не рушит замер
            print(f"{audio.name}: ошибка {error}", flush=True)
            continue
        rows.append(row)
        print(f"[{number}/{len(files)}] {audio.name}: root {row['root']:.0%}, "
              f"majmin {row['majmin']:.0%}", flush=True)

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    def weighted(group, metric):
        total = sum(r["duration"] for r in group)
        return sum(r[metric] * r["duration"] for r in group) / total if total else 0.0

    print(f"\nЗаписей: {len(rows)}, {time.time() - started:.0f} с")
    print(f"{'стиль':8}" + "".join(f"{m:>10}" for m in METRICS))
    styles = sorted({r["style"] for r in rows})
    for style in styles + ["ВСЕ"]:
        group = rows if style == "ВСЕ" else [r for r in rows if r["style"] == style]
        print(f"{style:8}" + "".join(f"{weighted(group, m):10.1%}" for m in METRICS))


if __name__ == "__main__":
    main()
