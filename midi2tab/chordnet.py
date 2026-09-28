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


# Подсказка тональности в споре мажор/минор. Замер (chord-eval, 28.09.2026):
# GuitarSet 0.911 -> 0.910, AAM 0.696 -> 0.693 при 2.5 -- почти без разницы;
# включена по просьбе владельца как проверка на живых песнях. Выключить --
# NASLUX_CHORD_KEY=0 в окружении сервера, без выкладки кода.
KEY_STRENGTH = float(os.environ.get("NASLUX_CHORD_KEY", "2.5") or 0)


def parallel(index: int) -> int:
    """Пара с тем же основным тоном: C <-> Cm, C7 <-> Cm7; maj7 -- без пары."""
    quality, root = divmod(index, 12)
    pair = {0: 1, 1: 0, 2: 4, 4: 2}.get(quality)
    return index if pair is None else pair * 12 + root


def with_key(probs, key, chroma=None, strength: float = KEY_STRENGTH):
    """Тональность решает спор «мажор или минор» там, где сеть сомневается.

    Сеть хорошо слышит основной тон, а терцию в плотном миксе с голосом --
    хуже (Dm -> D). Тональность же слышна по всей песне сразу. Штраф
    получает только аккорд вне тональности, у которого пара с тем же тоном
    -- в тональности, и тем сильнее, чем меньше сеть уверена в кадре:
    уверенный D в ре миноре (гармоническая доминанта, заимствование)
    остаётся D."""
    import numpy as np

    if not strength or key is None:
        return probs
    from . import audiochords

    k = probs.shape[1]
    names = [class_name(i, k) for i in range(k - 1)]
    outside = audiochords.key_penalties(names, key, chroma, strength=1.0)
    penalty = np.array([outside[i] if outside[parallel(i)] < outside[i] else 0.0 for i in range(k - 1)])
    doubt = np.minimum(1.0, 2.0 * (1.0 - probs.max(axis=1, keepdims=True)))
    out = probs.copy()
    out[:, :-1] *= np.exp(-strength * doubt * penalty[None, :])
    return out / out.sum(axis=1, keepdims=True)


def decode(probs, change_penalty: float = 5.0) -> list[tuple[float, float, str, float]]:
    """Вероятности по кадрам -> [(начало, конец, аккорд, уверенность)] без «N»."""
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


def detect(y, sr: int, change_penalty: float = 5.0, key=None, chroma=None,
           key_strength: float = KEY_STRENGTH) -> list[tuple[float, float, str, float]]:
    """Запись -> [(начало, конец, аккорд, уверенность)], без участков «N»;
    key -- (тон, мажор ли) песни для подсказки мажор/минор."""
    return decode(with_key(probabilities(y, sr), key, chroma, key_strength), change_penalty)
