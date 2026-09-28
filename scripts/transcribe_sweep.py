"""Подбор порогов распознавания нот (Basic Pitch) по эталону GuitarSet.

Модель прогоняется по записи один раз, дальше из её выхода собираются ноты
при разных порогах: атака (onset), удержание (frame), минимальная длина,
минимальная громкость. Метрика -- F1 нот (высота + атака ±50 мс), отдельно
для аккомпанемента и соло. Итог -- таблица лучших сочетаний.

    python scripts/transcribe_sweep.py --audio gs/audio --annotations gs/annotation --files 30
"""
import argparse
import itertools
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from midi_compare import reference  # noqa: E402

FRAMES_PER_SECOND = 22050 / 256


def f1(ref, notes) -> float:
    import mir_eval

    if not notes:
        return 0.0
    est = (np.array([[n[0], n[1]] for n in notes]),
           np.array([440.0 * 2 ** ((n[2] - 69) / 12) for n in notes]))
    return mir_eval.transcription.precision_recall_f1_overlap(
        ref[0], ref[1], est[0], est[1], onset_tolerance=0.05, offset_ratio=None)[2]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audio", required=True)
    parser.add_argument("--annotations", required=True)
    parser.add_argument("--files", type=int, default=30)
    parser.add_argument("--out", default="sweep.json")
    args = parser.parse_args()
    from basic_pitch import note_creation
    from basic_pitch.inference import run_inference

    from midi2tab import audioin

    # Поровну аккомпанементов и соло, по всем стилям и гитаристам
    files = sorted(Path(args.audio).glob("*_mic.wav"))
    comp = [f for f in files if "_comp_" in f.name][:: max(1, 180 // (args.files // 2))][: args.files // 2]
    solo = [f for f in files if "_solo_" in f.name][:: max(1, 180 // (args.files // 2))][: args.files // 2]
    grid = list(itertools.product([0.4, 0.5, 0.6, 0.7], [0.2, 0.3, 0.4], [60, 90, 130], [0.0, 0.2, 0.3]))
    scores = {g: {"comp": [], "solo": []} for g in grid}
    for number, audio in enumerate(comp + solo, 1):
        kind = "comp" if "_comp_" in audio.name else "solo"
        ref = reference(Path(args.annotations) / audio.name.replace("_mic.wav", ".jams"))
        output = run_inference(str(audio), audioin._model_path())
        for onset, frame, min_ms, min_amp in grid:
            _, events = note_creation.model_output_to_notes(
                output, onset_thresh=onset, frame_thresh=frame,
                min_note_len=int(round(min_ms / 1000 * FRAMES_PER_SECOND)),
                min_freq=82.0, max_freq=1400.0, melodia_trick=True, include_pitch_bends=False)
            notes = [e for e in events if e[3] >= min_amp]
            scores[(onset, frame, min_ms, min_amp)][kind].append(f1(ref, notes))
        print(f"[{number}/{len(comp) + len(solo)}] {audio.name}", flush=True)
    rows = []
    for g, s in scores.items():
        c, so = float(np.mean(s["comp"])), float(np.mean(s["solo"]))
        rows.append({"onset": g[0], "frame": g[1], "min_ms": g[2], "min_amp": g[3],
                     "comp": round(c, 3), "solo": round(so, 3), "mean": round((c + so) / 2, 3)})
    rows.sort(key=lambda r: -r["mean"])
    now = next(r for r in rows if (r["onset"], r["frame"], r["min_ms"], r["min_amp"]) == (0.5, 0.3, 90, 0.0))
    print("\nсейчас на сайте:", now)
    print("лучшие:")
    for r in rows[:10]:
        print(r)
    Path(args.out).write_text(json.dumps({"current": now, "top": rows[:30]}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
