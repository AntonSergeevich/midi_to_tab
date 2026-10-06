"""Разметка аккордов живых песен моделью сайта -- «учитель» для noisy student.

У отрывков FMA нет разметки аккордов. Её ставит модель, что сейчас на
сайте, причём осторожно: среднее по трём транспозициям (звук на полутон
ниже, как есть и на полутон выше), сглаживание Витерби и только те кадры,
где учитель уверен (остальные -- IGNORE, их не учим). Ученик учится на
этих метках с шумом аугментаций и на настоящей разметке GuitarSet/AAM --
и привыкает к голосу и живому сведению, которых учитель почти не слышал.

    CHORDNET_VOCAB=ext python pseudo.py --audio fma --model midi2tab/models/chordnet.onnx --out data
"""
from __future__ import annotations

import argparse
import os
import sys
from multiprocessing import Pool
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402

SHIFTS = (-1, 0, 1)
CONFIDENT = 0.6           # кадр учим, если учитель за свой аккорд не меньше чем на 60%
MIN_LABELED = 0.4         # отрывок берём, если размечено хотя бы 40% кадров
PENALTY = 3.0             # штраф смены аккорда, как на проверке GuitarSet

_session = None


def teacher(x: np.ndarray, model: str) -> np.ndarray:
    """Вероятности классов, среднее по транспозициям (вернутым к исходной)."""
    global _session
    if _session is None:
        import onnxruntime as ort

        options = ort.SessionOptions()
        options.intra_op_num_threads = 1
        _session = ort.InferenceSession(model, options, providers=["CPUExecutionProvider"])
    total = None
    for d in SHIFTS:
        start = common.CENTER + 2 * d     # окно выше на d = звук ниже на d
        logits = _session.run(None, {"cqt": x[None, :, start:start + common.WINDOW]})[0][0]
        logits = logits - logits.max(axis=1, keepdims=True)
        p = np.exp(logits)
        p /= p.sum(axis=1, keepdims=True)
        back = np.empty_like(p)           # класс (q, r) при сдвиге d -> (q, r + d)
        back[:, -1] = p[:, -1]
        for q in range(common.N_CLASS // 12):
            back[:, q * 12:(q + 1) * 12] = np.roll(p[:, q * 12:(q + 1) * 12], d, axis=1)
        total = back if total is None else total + back
    return total / len(SHIFTS)


def one(task):
    audio, target, model = task
    if os.path.exists(target):
        return "есть"
    import librosa

    try:
        y, _ = librosa.load(audio, sr=common.SR, mono=True)
    except Exception:  # noqa: BLE001 -- битый mp3
        return "битый"
    finally:
        os.remove(audio)
    if len(y) < common.SR * 10:
        return "короткий"
    x = common.features(y)
    probs = teacher(x, model)
    if probs.shape[1] != common.N_CLASS + 1:
        raise SystemExit(f"словарь учителя {probs.shape[1]} классов, а у обучения {common.N_CLASS + 1}")
    path = common.viterbi(probs, PENALTY)
    sure = probs[np.arange(len(path)), path]
    labels = np.where(sure >= CONFIDENT, path, common.IGNORE).astype(np.int16)
    chord = (labels >= 0) & (labels < common.N_CLASS)
    if chord.mean() < MIN_LABELED:
        return f"неуверенно {int(chord.mean() * 10) * 10}%"
    np.savez_compressed(target, x=x.astype(np.float16), y=labels)
    return "ок"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audio", default="fma")
    parser.add_argument("--model", default="midi2tab/models/chordnet.onnx")
    parser.add_argument("--out", default="data")
    args = parser.parse_args()
    os.makedirs(args.out, exist_ok=True)
    tasks = [(str(a), f"{args.out}/{a.stem}.npz", args.model) for a in sorted(Path(args.audio).glob("fma_*.mp3"))]
    counts = {}
    with Pool(max(1, os.cpu_count() or 1)) as pool:
        for number, result in enumerate(pool.imap_unordered(one, tasks), 1):
            counts[result] = counts.get(result, 0) + 1
            if number % 100 == 0:
                print(f"разметка: {number}/{len(tasks)} {counts}", flush=True)
    print(f"::notice title=FMA разметка::{counts}", flush=True)


if __name__ == "__main__":
    main()
