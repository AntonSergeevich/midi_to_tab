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
maj7 и m7, 121 -- ещё sus4, sus2, dim, aug и 6.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

# Модель с расширенными аккордами: 121 класс (к мажору/минору и септаккордам
# добавлены sus4, sus2, dim, aug, 6), GuitarSet + 1940 песен AAM (было 500)
# + 15% синтетики. Замеры 30.09.2026 против прошлой (ext15, 500 песен AAM):
# свежая синтетика majmin 0.870 -> 0.875, mirex 0.850 -> 0.862, септаккорды
# 0.710 -> 0.728; sus4 59% -> 77%, dim 77% -> 78%, aug 59% -> 60%, sus2 64%,
# 6 11% -> 0%. Независимо (chord-eval, подсказка тональности 2.5): гитара
# соло GuitarSet 0.890 -> 0.885, неслышанный блок AAM 0.956 -> 0.959.
# Откат без выкладки кода: NASLUX_CHORD_MODEL=ext15 (прошлая), r2 (61 класс)
# или v2 (самая первая).
_MODELS = Path(__file__).resolve().parent / "models"
_VARIANTS = {"ext15": "chordnet_ext15.onnx", "r2": "chordnet_r2.onnx", "v2": "chordnet_v2.onnx"}
MODEL = _MODELS / _VARIANTS.get(os.environ.get("NASLUX_CHORD_MODEL", ""), "chordnet.onnx")

# Должно совпадать с scripts/chordnet/common.py
SR = 22050
HOP = 2048
BINS_PER_OCTAVE = 24
N_BINS = 168
WINDOW = 144
CENTER = 12
NOTES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
# Первые пять -- словарь sevenths (61 класс), дальше -- расширенный (121)
QUALITIES = ["maj", "min", "7", "maj7", "min7", "sus4", "sus2", "dim", "aug", "maj6"]
SUFFIX = {"maj": "", "min": "m", "7": "7", "maj7": "maj7", "min7": "m7",
          "sus4": "sus4", "sus2": "sus2", "dim": "dim", "aug": "aug", "maj6": "6"}


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


@lru_cache(maxsize=4)
def _session_for(path: str):
    """Сессия конкретного файла модели -- для сверки моделей на одной песне,
    не трогая ту, что работает на сайте."""
    import onnxruntime as ort

    options = ort.SessionOptions()
    options.intra_op_num_threads = int(os.environ.get("MIDI2TAB_THREADS", "1") or 1)
    return ort.InferenceSession(path, options, providers=["CPUExecutionProvider"])


def variants() -> dict[str, Path]:
    """Модели, что лежат рядом: «main» -- на сайте, остальные -- для отката."""
    found = {"main": MODEL} if MODEL.is_file() else {}
    for name, file in _VARIANTS.items():
        if (_MODELS / file).is_file() and _MODELS / file != MODEL:
            found[name] = _MODELS / file
    return found


def features(y, sr: int):
    import librosa
    import numpy as np

    if sr != SR:
        y = librosa.resample(y, orig_sr=sr, target_sr=SR)
    cqt = np.abs(librosa.cqt(y, sr=SR, hop_length=HOP, fmin=librosa.note_to_hz("C1"),
                             n_bins=N_BINS, bins_per_octave=BINS_PER_OCTAVE))
    spec = np.log1p(100.0 * cqt).T.astype(np.float32)
    return (spec - spec.mean()) / (spec.std() + 1e-6)


def probabilities(y, sr: int, model: Path | None = None):
    """Вероятности классов по кадрам (~10.8 кадра в секунду)."""
    import numpy as np

    x = features(y, sr)[:, CENTER:CENTER + WINDOW]
    session = _session() if model is None else _session_for(str(model))
    logits = session.run(None, {"cqt": x[None]})[0][0]
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


# Подсказка тональности в споре мажор/минор. Сила 2.5 была откалибрована
# 28.09.2026 ("почти без разницы", GuitarSet 0.911 -> 0.910, AAM 0.696 ->
# 0.693) ДО переобучения на 1940 песнях AAM -- и с него больше не годится:
# повторный замер на уже обученной (текущей) модели (chord-eval, прогон
# 36778594860, 30.09.2026; сила 0/0.7/1.5/2.5/4.0) показал, что новая сеть
# куда чувствительнее к этому штрафу -- на силе 2.5 AAM majmin садится до
# 0.960 против 0.967 без подсказки вовсе, а GuitarSet почти не выигрывает
# (0.885 против 0.885 на 0.0). Сила 0.7 -- единственная проверенная
# точка, где нет такого провала: GuitarSet majmin 0.887 (лучшая из всех),
# AAM 0.967 (вровень с выключенной подсказкой). Выключить -- NASLUX_CHORD_KEY=0
# в окружении сервера, без выкладки кода.
KEY_STRENGTH = float(os.environ.get("NASLUX_CHORD_KEY", "0.7") or 0)


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
           key_strength: float = KEY_STRENGTH, model: Path | None = None) -> list[tuple[float, float, str, float]]:
    """Запись -> [(начало, конец, аккорд, уверенность)], без участков «N»;
    key -- (тон, мажор ли) песни для подсказки мажор/минор; model -- другой
    файл модели вместо того, что на сайте."""
    return decode(with_key(probabilities(y, sr, model), key, chroma, key_strength), change_penalty)
