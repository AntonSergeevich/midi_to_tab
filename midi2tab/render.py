"""
Озвучка MIDI в звук прямо на сервере -- чтобы плеер мог её проиграть.

Браузер MIDI не играет, а синтезатора (fluidsynth и банка звуков) на
сервере нет. Поэтому звук собирается здесь: щипковый тембр из нескольких
гармоник с затуханием (на гитару похоже больше, чем голый синус), ударные --
коротким шумом, бочка -- низким «бумом». Для репетиции и сверки табов
этого хватает; mp3 делает ffmpeg.
"""

from __future__ import annotations

import os
import subprocess
import tempfile

SR = 22050
HARMONICS = (1.0, 0.5, 0.33, 0.22, 0.14, 0.09)
MAX_SECONDS = 600


def _pluck(pitch: int, length: float, velocity: int):
    import numpy as np

    n = max(1, int(length * SR))
    t = np.arange(n) / SR
    freq = 440.0 * 2 ** ((pitch - 69) / 12)
    tone = np.zeros(n)
    for k, weight in enumerate(HARMONICS, 1):
        if freq * k > SR / 2:
            break
        # высокие гармоники гаснут быстрее -- звук «щипка», а не органа
        tone += weight * np.sin(2 * np.pi * freq * k * t) * np.exp(-t * (1.2 + 1.1 * k))
    attack = np.minimum(1.0, t / 0.004)
    return tone * attack * (velocity / 127) * 0.22


def _drum(pitch: int, velocity: int):
    import numpy as np

    rng = np.random.default_rng(pitch)
    if pitch in (35, 36):                                     # бочка
        t = np.arange(int(0.25 * SR)) / SR
        hit = np.sin(2 * np.pi * (55 + 60 * np.exp(-t * 30)) * t) * np.exp(-t * 14)
        gain = 0.5
    else:                                                     # прочие -- шум
        decay = 30 if pitch in (42, 44, 46) else 14           # хэт короче
        t = np.arange(int(0.2 * SR)) / SR
        hit = rng.standard_normal(len(t)) * np.exp(-t * decay)
        gain = 0.12
    return hit * (velocity / 127) * gain


def midi_to_mp3(midi_path: str, mp3_path: str) -> str:
    """Озвучить MIDI в mp3; возвращает путь к mp3."""
    import numpy as np
    import pretty_midi
    import soundfile as sf

    pm = pretty_midi.PrettyMIDI(midi_path)
    end = min(pm.get_end_time(), MAX_SECONDS)
    out = np.zeros(int((end + 2.0) * SR))
    for inst in pm.instruments:
        for note in inst.notes:
            if note.start >= end:
                continue
            start = int(note.start * SR)
            if inst.is_drum:
                sound = _drum(note.pitch, note.velocity)
            else:
                held = max(0.08, min(note.end - note.start, 4.0))
                sound = _pluck(note.pitch, held + 0.35, note.velocity)
                fade = int(0.35 * SR)                         # отпустили струну -- тише
                sound[-fade:] *= np.linspace(1.0, 0.0, min(fade, len(sound)))
            stop = min(len(out), start + len(sound))
            out[start:stop] += sound[:stop - start]
    peak = float(np.max(np.abs(out))) or 1.0
    out = out / peak * 0.89
    with tempfile.TemporaryDirectory() as work:
        wav = os.path.join(work, "render.wav")
        sf.write(wav, out.astype(np.float32), SR)
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", wav, "-b:a", "128k", mp3_path],
                       check=True, timeout=300)
    return mp3_path
