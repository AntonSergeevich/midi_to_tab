"""Сравнить гармонию переделок с исходником -- числами, а не на слух.

Нейросеть может сделать красивые по звуку инструменты, но «кашу» по
гармонии: каждый играет своё. Это видно и в цифрах:
  ключ      -- насколько звук держится одной тональности (0..1, выше лучше);
  увер.     -- средняя уверенность распознавателя аккордов NASLUX;
  смен/мин  -- смен аккорда в минуту: у каши их в разы больше, чем в песне;
  с исх.    -- доля времени, когда основа аккорда совпадает с исходником.

    python scripts/harmony_eval.py source.mp3 version1.mp3 version2.mp3 ...
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from midi2tab import audiochords  # noqa: E402

NOTES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
FLATS = {"Db": "C#", "Eb": "D#", "Gb": "F#", "Ab": "G#", "Bb": "A#"}


def root(name: str) -> int | None:
    if not name or name in ("N", "N.C."):
        return None
    head = name[:2] if len(name) > 1 and name[1] in "#b" else name[:1]
    head = FLATS.get(head, head)
    return NOTES.index(head) if head in NOTES else None


def key_clarity(path: str) -> float:
    import librosa
    import numpy as np

    y, sr = librosa.load(path, sr=22050, mono=True)
    profile = librosa.feature.chroma_cqt(y=librosa.effects.harmonic(y), sr=sr).mean(axis=1)
    return max(float(np.corrcoef(profile, np.roll(w, k))[0, 1])
               for k in range(12)
               for w in (audiochords.KRUMHANSL_MAJOR, audiochords.KRUMHANSL_MINOR))


def roots_timeline(chords, duration: float, step: float = 0.5):
    out, i = [], 0
    t = 0.0
    while t < duration:
        while i + 1 < len(chords) and chords[i].end <= t:
            i += 1
        c = chords[i] if chords and chords[i].start <= t < chords[i].end else None
        out.append(root(c.name) if c else None)
        t += step
    return out


def analyse(path: str) -> dict:
    result = audiochords.detect_from_audio(path)
    chords = result.chords if hasattr(result, "chords") else result[0]
    duration = chords[-1].end if chords else 0.0
    return {
        "chords": chords, "duration": duration,
        "key": key_clarity(path),
        "conf": sum(c.confidence * (c.end - c.start) for c in chords) / max(duration, 1e-6),
        "changes": len(chords) / max(duration / 60, 1e-6),
        "names": " ".join(c.name for c in chords[:12]),
    }


def main() -> None:
    source, *versions = sys.argv[1:]
    base = analyse(source)
    base_roots = roots_timeline(base["chords"], base["duration"])
    print(f"{'файл':32} {'ключ':>5} {'увер.':>6} {'смен/мин':>9} {'с исх.':>7}  начало разбора")
    for path in [source, *versions]:
        a = base if path == source else analyse(path)
        roots = roots_timeline(a["chords"], min(a["duration"], base["duration"]))
        pairs = [(x, y) for x, y in zip(roots, base_roots) if x is not None and y is not None]
        agree = sum(x == y for x, y in pairs) / len(pairs) if pairs else 0.0
        print(f"{Path(path).name[:32]:32} {a['key']:5.2f} {a['conf']:6.2f} {a['changes']:9.1f} "
              f"{agree:7.0%}  {a['names']}")


if __name__ == "__main__":
    main()
