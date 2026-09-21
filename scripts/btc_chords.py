#!/usr/bin/env python3
"""
Аккорды моделью BTC-ISMIR19 -- для сравнения с нашим разбором.

Что делает: берёт аудио, считает постоянное-Q преобразование, прогоняет
через предобученный двунаправленный трансформер и возвращает список
    [{"start": float, "end": float, "chord": str}, ...]

Веса берутся из проекта ChordMini (лицензия MIT) -- это оригинальные
веса авторов BTC для большого словаря аккордов, 12 МБ. Скачиваются один
раз при первом запуске.

Запуск:
    python scripts/btc_chords.py песня.mp3
    python scripts/btc_chords.py песня.mp3 --json итог.json

ЗАМЕР НА РАЗМЕЧЕННЫХ ПЕСНЯХ (три песни, круг аккордов назван вручную):

                         BTC          наш разбор
    названия аккордов    56.9%          99.5%
    основные тоны        94.1%         100.0%

Расхождение не в архитектуре: корни BTC слышит почти так же хорошо. Он
ошибается в КАЧЕСТВЕ -- пишет G вместо Gm, D вместо Dm, Gm7 вместо Gm,
потому что не смотрит на тональность. Ровно та же ошибка была и у нас,
пока лад не стал доводом при выборе качества.

Скрипт оставлен в репозитории намеренно: чтобы это можно было
перепроверить на своих песнях, а не верить на слово.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path

WEIGHTS_URL = (
    "https://raw.githubusercontent.com/ptnghia-j/ChordMini/main/"
    "checkpoints/btc_model_large_voca.pt"
)
CODE_URL = "https://github.com/ptnghia-j/ChordMini.git"

# Параметры признаков -- те же, на которых модель обучалась. Менять
# нельзя: модель видела ровно такое постоянное-Q преобразование.
SAMPLE_RATE = 22050
N_BINS = 144
BINS_PER_OCTAVE = 24
HOP_LENGTH = 2048

FLATS = {"Db": "C#", "Eb": "D#", "Gb": "F#", "Ab": "G#", "Bb": "A#",
         "Cb": "B", "Fb": "E"}
QUALITIES = {"": "", "maj": "", "min": "m", "7": "7", "min7": "m7",
             "maj7": "maj7", "sus4": "sus4", "sus2": "sus2", "dim": "dim",
             "aug": "aug", "maj6": "6", "min6": "m6", "hdim7": "m7b5",
             "dim7": "dim7", "minmaj7": "mmaj7"}


def ensure_code(root: Path) -> Path:
    """Скачать код ChordMini, если его ещё нет."""
    target = root / "ChordMini"
    if target.is_dir():
        return target
    import subprocess

    print(f"Скачиваю код модели в {target} ...", file=sys.stderr)
    subprocess.run(
        ["git", "clone", "--depth", "1", "--filter=blob:limit=2m", CODE_URL, str(target)],
        check=True,
    )
    return target


def ensure_weights(code_dir: Path) -> Path:
    path = code_dir / "checkpoints" / "btc_model_large_voca.pt"
    if path.is_file():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    print("Скачиваю веса модели (12 МБ) ...", file=sys.stderr)
    urllib.request.urlretrieve(WEIGHTS_URL, path)
    return path


def tidy(label: str) -> str | None:
    """C:min -> Cm, Db -> C#, N (тишина) -> None."""
    if label in ("N", "X"):
        return None
    root, _, quality = label.partition(":")
    root = FLATS.get(root, root)
    return root + QUALITIES.get(quality, quality)


def chords(audio_path: str, cache: str | None = None) -> list[dict]:
    """Разобрать запись. Возвращает [{"start", "end", "chord"}, ...]."""
    import numpy as np
    import librosa
    import torch

    root = Path(cache or os.environ.get("BTC_CACHE") or Path.home() / ".cache" / "btc")
    root.mkdir(parents=True, exist_ok=True)
    code_dir = ensure_code(root)
    weights = ensure_weights(code_dir)
    sys.path.insert(0, str(code_dir))

    from src.models import BTC_model                    # noqa: E402
    from src.models.common.config import ModelConfig    # noqa: E402
    from src.utils import idx2voca_chord                # noqa: E402

    state = torch.load(weights, map_location="cpu", weights_only=False)
    config = ModelConfig()
    for field, value in (("feature_size", N_BINS), ("num_chords", 170)):
        if hasattr(config, field):
            setattr(config, field, value)
    model = BTC_model(config=config)
    model.load_state_dict(state["model"], strict=False)
    model.eval()

    y, _ = librosa.load(audio_path, sr=SAMPLE_RATE)
    feature = librosa.cqt(y, sr=SAMPLE_RATE, n_bins=N_BINS,
                          bins_per_octave=BINS_PER_OCTAVE, hop_length=HOP_LENGTH)
    feature = np.log(np.abs(feature) + 1e-6)
    feature = ((feature - state["mean"]) / state["std"]).T

    with torch.no_grad():
        frames = model.predict(
            torch.tensor(feature[None], dtype=torch.float32),
            per_frame=True, smooth=True, kernel_size=17,
        ).squeeze(0).tolist()

    table = idx2voca_chord()
    step = HOP_LENGTH / SAMPLE_RATE
    spans: list[dict] = []
    for index, state_id in enumerate(frames):
        name = tidy(table[state_id])
        if name is None:
            continue
        start = index * step
        if spans and spans[-1]["chord"] == name and abs(spans[-1]["end"] - start) < step * 1.5:
            spans[-1]["end"] = start + step
        else:
            spans.append({"start": round(start, 3), "end": round(start + step, 3),
                          "chord": name})
    for span in spans:
        span["start"] = round(span["start"], 3)
        span["end"] = round(span["end"], 3)
    return spans


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument("audio", help="файл .mp3, .wav, .flac, .ogg или .m4a")
    parser.add_argument("--json", help="куда сохранить результат")
    parser.add_argument("--cache", help="куда класть код и веса модели")
    args = parser.parse_args()

    spans = chords(args.audio, args.cache)
    if args.json:
        Path(args.json).write_text(
            json.dumps(spans, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"Сохранено: {args.json} ({len(spans)} отрезков)")
    else:
        for span in spans:
            print(f'{span["start"]:8.2f} - {span["end"]:8.2f}  {span["chord"]}')
        print(f"\nВсего отрезков: {len(spans)}, "
              f"разных аккордов: {len({s['chord'] for s in spans})}", file=sys.stderr)


if __name__ == "__main__":
    main()
