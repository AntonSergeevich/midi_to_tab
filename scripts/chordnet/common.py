"""Общее для обучения нейросети аккордов (scripts/chordnet) и для разбора на
сайте (midi2tab/chordnet.py): признаки, классы, декодирование.

Классы -- словарь MIREX majmin: 12 мажоров, 12 миноров и «нет аккорда» (N).
Признаки -- CQT по 24 полосы на октаву (2 на полутон) с запасом в пол-октавы
сверху и снизу: при обучении окно из 144 полос сдвигается на 0..11 полутонов,
и одна запись превращается в двенадцать транспозиций.
"""
from __future__ import annotations

import numpy as np

SR = 22050
HOP = 2048                      # ~10.8 кадра в секунду
BINS_PER_OCTAVE = 24
N_BINS = 168                    # 7 октав от C1: 6 рабочих + по пол-октавы запаса
WINDOW = 144                    # полос в окне модели (6 октав)
CENTER = 12                     # сдвиг окна при разборе: середина запаса
NOTES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
N_CLASS = 24
CLASSES = [f"{n}:maj" for n in NOTES] + [f"{n}:min" for n in NOTES] + ["N"]
IGNORE = -100


def features(y: np.ndarray, sr: int = SR) -> np.ndarray:
    """Кадры x полосы: логарифм амплитуды CQT, нормированный по записи."""
    import librosa

    if sr != SR:
        y = librosa.resample(y, orig_sr=sr, target_sr=SR)
    cqt = np.abs(librosa.cqt(y, sr=SR, hop_length=HOP, fmin=librosa.note_to_hz("C1"),
                             n_bins=N_BINS, bins_per_octave=BINS_PER_OCTAVE))
    spec = np.log1p(100.0 * cqt).T.astype(np.float32)
    return (spec - spec.mean()) / (spec.std() + 1e-6)


def frame_times(n_frames: int) -> np.ndarray:
    return (np.arange(n_frames) + 0.5) * HOP / SR


def class_of(label: str) -> int:
    """Harte-подпись -> класс majmin; аккорды вне словаря (sus, dim, aug) --
    IGNORE: их не учим и не оцениваем, как в MIREX."""
    import mir_eval

    if label in ("N", ""):
        return N_CLASS
    if label == "X":
        return IGNORE
    try:
        root, bitmap, _ = mir_eval.chord.encode(label)
    except Exception:  # noqa: BLE001
        return IGNORE
    if root < 0:
        return N_CLASS
    if bitmap[4] and bitmap[7]:
        return int(root)
    if bitmap[3] and bitmap[7]:
        return 12 + int(root)
    return IGNORE


def frame_labels(segments, n_frames: int) -> np.ndarray:
    """[(начало, конец, подпись)] -> класс каждого кадра (по центру кадра)."""
    labels = np.full(n_frames, N_CLASS, dtype=np.int16)
    times = frame_times(n_frames)
    for start, end, label in segments:
        labels[(times >= start) & (times < end)] = class_of(label)
    return labels


def transpose_class(cls: np.ndarray, semitones: int) -> np.ndarray:
    """Классы после сдвига звука на semitones вверх."""
    out = cls.copy()
    chord = (cls >= 0) & (cls < N_CLASS)
    out[chord] = (cls[chord] // 12) * 12 + (cls[chord] % 12 + semitones) % 12
    return out


def viterbi(probs: np.ndarray, change_penalty: float = 3.0) -> np.ndarray:
    """Самый вероятный путь по кадрам: смена аккорда стоит change_penalty
    (в натуральных логарифмах) -- убирает дребезг кадр-в-кадр."""
    logp = np.log(probs + 1e-9)
    n, k = logp.shape
    score = logp[0].copy()
    back = np.zeros((n, k), dtype=np.int32)
    for t in range(1, n):
        best = int(score.argmax())
        stay = score
        move = score[best] - change_penalty
        back[t] = np.where(stay >= move, np.arange(k), best)
        score = np.maximum(stay, move) + logp[t]
    path = np.empty(n, dtype=np.int32)
    path[-1] = int(score.argmax())
    for t in range(n - 1, 0, -1):
        path[t - 1] = back[t, path[t]]
    return path


def segments(path: np.ndarray, min_frames: int = 3):
    """Путь по кадрам -> [(начало, конец, подпись)]; совсем короткие куски
    приклеиваются к соседу."""
    edges = [0] + [t for t in range(1, len(path)) if path[t] != path[t - 1]] + [len(path)]
    parts = [[edges[i], edges[i + 1], int(path[edges[i]])] for i in range(len(edges) - 1)]
    merged = []
    for part in parts:
        if merged and (part[1] - part[0] < min_frames or merged[-1][2] == part[2]):
            merged[-1][1] = part[1]
        else:
            merged.append(part)
    step = HOP / SR
    return [(a * step, b * step, CLASSES[c]) for a, b, c in merged]
