"""Минутные «песни» с известным темпом: бочка, малый, хай-хэт и аккорды.

Прямые восьмые и шаффл (триольные хай-хэты) -- на шаффле librosa путает
пульсацию: 81 читается как 123, ровные 81 -- как 161. Имя файла несёт
темп: synth_081_shuffle.wav.
"""

import argparse
from pathlib import Path

import numpy as np
import soundfile as sf

SR = 22050
CHORDS = [[62, 65, 69], [60, 64, 67], [58, 62, 65], [57, 61, 64]]


def song(bpm: float, shuffle: bool, seed: int, seconds: float = 60.0):
    rng = np.random.default_rng(seed)
    y = np.zeros(int(SR * seconds))
    beat = 60.0 / bpm

    def hit(t, kind, amp=1.0):
        i, n = int(t * SR), int(0.15 * SR)
        if i + n >= len(y):
            return
        tt = np.arange(n) / SR
        if kind == "kick":
            s = np.sin(2 * np.pi * (50 + 80 * np.exp(-tt * 30)) * tt) * np.exp(-tt * 18)
        elif kind == "snare":
            s = rng.normal(0, 1, n) * np.exp(-tt * 25) * 0.6 + np.sin(2 * np.pi * 190 * tt) * np.exp(-tt * 20) * 0.4
        else:
            s = rng.normal(0, 1, n) * np.exp(-tt * 80) * 0.25
        y[i:i + n] += amp * s

    k = 0
    while k * beat < seconds - 1:
        t, pos = k * beat, k % 4
        hit(t, "kick" if pos in (0, 2) else "snare")
        for sub in ([0, 1 / 3, 2 / 3] if shuffle else [0, 0.5]):
            hit(t + sub * beat, "hat", 0.8 if sub == 0 else 0.5)
        k += 1
    t = np.arange(len(y)) / SR
    bar = 4 * beat
    for b in range(int(seconds / bar) + 1):
        a, e = int(b * bar * SR), int(min(seconds, (b + 1) * bar) * SR)
        for m in CHORDS[b % 4]:
            y[a:e] += 0.05 * np.sin(2 * np.pi * 440 * 2 ** ((m - 69) / 12) * t[a:e])
    return y / np.abs(y).max() * 0.9


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="synth_beats")
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for i, bpm in enumerate(range(60, 181, 8)):
        for shuffle in (False, True):
            name = f"synth_{bpm:03d}_{'shuffle' if shuffle else 'straight'}.wav"
            sf.write(out / name, song(bpm, shuffle, seed=i), SR)
    print("синтетика:", len(list(out.glob("*.wav"))), "песен")


if __name__ == "__main__":
    main()
