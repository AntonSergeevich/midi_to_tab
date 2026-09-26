"""Признаки и покадровая разметка: GuitarSet (JAMS) и синтетика (json) -> npz.

    python prepare.py --guitarset gs/audio_mic gs/audio_mix --annotations gs/annotation \\
        --synth synth --out data
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from multiprocessing import Pool
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402


def guitarset_segments(jams_path: Path):
    data = json.loads(jams_path.read_text())
    chords = [a for a in data["annotations"] if a["namespace"] == "chord"][0]["data"]
    return [(c["time"], c["time"] + c["duration"], c["value"]) for c in chords]


def one(task):
    audio, segments, target, delete = task
    if os.path.exists(target):
        return target
    import librosa

    y, _ = librosa.load(audio, sr=common.SR, mono=True)
    x = common.features(y)
    labels = common.frame_labels(segments, len(x))
    np.savez_compressed(target, x=x.astype(np.float16), y=labels)
    if delete:
        os.remove(audio)
    return target


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--guitarset", nargs="*", default=[])
    parser.add_argument("--annotations", default="")
    parser.add_argument("--synth", default="")
    parser.add_argument("--out", default="data")
    parser.add_argument("--delete-synth", action="store_true", help="удалять wav синтетики после разбора")
    args = parser.parse_args()
    os.makedirs(args.out, exist_ok=True)
    tasks = []
    for folder in args.guitarset:
        tag = Path(folder).name
        for audio in sorted(Path(folder).glob("*_comp_*.wav")):
            jams = Path(args.annotations) / (audio.name.split("_comp_")[0] + "_comp.jams")
            tasks.append((str(audio), guitarset_segments(jams),
                          f"{args.out}/gs_{tag}_{audio.stem}.npz", False))
    if args.synth:
        for labels in sorted(Path(args.synth).glob("*.json")):
            tasks.append((str(labels.with_suffix(".wav")), json.loads(labels.read_text()),
                          f"{args.out}/{labels.stem}.npz", args.delete_synth))
    with Pool(max(1, os.cpu_count() or 1)) as pool:
        for number, _ in enumerate(pool.imap_unordered(one, tasks), 1):
            if number % 100 == 0:
                print(f"признаки: {number}/{len(tasks)}", flush=True)
    print(f"готово: {len(tasks)} записей", flush=True)


if __name__ == "__main__":
    main()
