"""
Аккорды нейросетью (models/chordnet.onnx).

Свёрточно-рекуррентная сеть по CQT-спектрограмме, обученная на GuitarSet и
синтетике (scripts/chordnet, .github/workflows/chord-train.yml). Словарь --
61 класс: мажор, минор, 7, maj7, m7 и «нет аккорда». На песнях GuitarSet,
которых она не слышала ни у одного гитариста, majmin 0.91 против 0.31 у
прежнего разбора на шаблонах; сглаживание 5 -- лучшее на той проверке.

Здесь -- только разбор: те же признаки, что при обучении (не менять одно
без другого!), прогон через onnxruntime, сглаживание Витерби. Словарь
берётся из размера выхода модели: 25 классов -- мажор/минор, 61 -- ещё 7,
maj7 и m7.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

MODEL = Path(__file__).resolve().parent / "models" / "chordnet.onnx"

# Должно совпадать с scripts/chordnet/common.py
SR = 22050
HOP = 2048
BINS_PER_OCTAVE = 24
N_BINS = 168
WINDOW = 144
CENTER = 12
NOTES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
QUALITIES = ["maj", "min", "7", "maj7", "min7"]
SUFFIX = {"maj": "", "min": "m", "7": "7", "maj7": "maj7", "min7": "m7"}


def available() -> bool:
    """Модель на месте, onnxruntime есть и нейросеть не выключена
    (NASLUX_CHORD_ENGINE=classic возвращает прежний разбор)."""
    if os.environ.get("NASLUX_CHORD_ENGINE", "net") != "net" or not MODEL.is_file():
        return False
    try:
        import onnxruntime  # noqa: F401
    except ImportError:
        return False
    return True


@lru_cache(maxsize=1)
def _session():
    import onnxruntime as ort

    options = ort.SessionOptions()
    options.intra_op_num_threads = int(os.environ.get("MIDI2TAB_THREADS", "1") or 1)
    return ort.InferenceSession(str(MODEL), options, providers=["CPUExecutionProvider"])


def features(y, sr: int):
    import librosa
    import numpy as np

    if sr != SR:
        y = librosa.resample(y, orig_sr=sr, target_sr=SR)
    cqt = np.abs(librosa.cqt(y, sr=SR, hop_length=HOP, fmin=librosa.note_to_hz("C1"),
                             n_bins=N_BINS, bins_per_octave=BINS_PER_OCTAVE))
    spec = np.log1p(100.0 * cqt).T.astype(np.float32)
    return (spec - spec.mean()) / (spec.std() + 1e-6)


def probabilities(y, sr: int):
    """Вероятности классов по кадрам (~10.8 кадра в секунду)."""
    import numpy as np

    x = features(y, sr)[:, CENTER:CENTER + WINDOW]
    logits = _session().run(None, {"cqt": x[None]})[0][0]
    logits = logits - logits.max(axis=1, keepdims=True)
    p = np.exp(logits)
    return p / p.sum(axis=1, keepdims=True)


def class_name(index: int, n_classes: int) -> str:
    """Номер класса -> подпись NASLUX («C», «F#m», «G7»); последний класс -- N."""
    if index >= n_classes - 1:
        return "N"
    return NOTES[index % 12] + SUFFIX[QUALITIES[index // 12]]


def viterbi(probs, change_penalty: float = 2.0):
    """Самый вероятный путь: смена аккорда стоит change_penalty (натуральный
    логарифм) -- убирает дребезг кадр-в-кадр."""
    import numpy as np

    logp = np.log(probs + 1e-9)
    n, k = logp.shape
    score = logp[0].copy()
    back = np.zeros((n, k), dtype=np.int32)
    for t in range(1, n):
        best = int(score.argmax())
        move = score[best] - change_penalty
        back[t] = np.where(score >= move, np.arange(k), best)
        score = np.maximum(score, move) + logp[t]
    path = np.empty(n, dtype=np.int32)
    path[-1] = int(score.argmax())
    for t in range(n - 1, 0, -1):
        path[t - 1] = back[t, path[t]]
    return path


def detect(y, sr: int, change_penalty: float = 5.0) -> list[tuple[float, float, str, float]]:
    """Запись -> [(начало, конец, аккорд, уверенность)], без участков «N»."""
    probs = probabilities(y, sr)
    n_classes = probs.shape[1]
    path = viterbi(probs, change_penalty)
    step = HOP / SR
    out = []
    start = 0
    for t in range(1, len(path) + 1):
        if t == len(path) or path[t] != path[start]:
            name = class_name(int(path[start]), n_classes)
            if name != "N":
                out.append((start * step, t * step, name, float(probs[start:t, path[start]].mean())))
            start = t
    return out
