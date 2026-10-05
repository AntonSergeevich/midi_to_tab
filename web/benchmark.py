"""
Сверка разбора аккордов с эталоном, который вставил музыкант.

Музыкант загружает свой трек в «Мои треки» и вставляет аккорды песни так,
как они записаны на сайте аккордов (вместе с текстом -- текст тут же
отбрасывается, храним только аккорды). Сервер слушает трек всеми моделями,
что у него есть, и считает, насколько каждая совпала с эталоном. Так
лучшая модель выбирается по русским песням, а не только по учебным наборам.

Времени смены аккордов в листе нет, поэтому меры -- без таймингов:
  покрытие   -- доля звучания, где наш аккорд есть в эталоне песни;
  словарь    -- какую долю аккордов эталона мы нашли (хоть 2 секунды звучания);
  переходы   -- какую долю переходов эталона (Am -> F) мы услышали.
Всё -- на уровне «мажор/минор» с основным тоном: «Fm/C» считается Fm,
«A7» -- A. Если эталон записан в другом строе (каподастр, другая
тональность), лучший сдвиг ищется по всем 12 полутонам и показывается.
"""

from __future__ import annotations

import re

NOTE = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11, "H": 11}
NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
FLATS = ["C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B"]
CHORD = re.compile(r"^([A-H])(#|b)?(m(?!aj)|min)?(maj|dim|aug|sus|add)?(\d{0,2})(?:/([A-H])(#|b)?)?$")
SPLIT = re.compile(r"[\s|:,;()\[\]]+")
MAX_SHEET = 20000


def parse_sheet(text: str) -> list[str]:
    """Лист аккордов (с текстом песни) -> аккорды по порядку. Аккордом
    считается строка, в которой все слова -- аккорды: строки текста песни,
    где случайно встретилось «A» или «Да», отбрасываются целиком."""
    chords: list[str] = []
    for line in (text or "")[:MAX_SHEET].splitlines():
        words = [w for w in SPLIT.split(line.replace("Вступление", "").replace("Проигрыш", "")) if w]
        if words and all(CHORD.match(w) for w in words):
            chords += words
    return chords


def majmin(chord: str) -> tuple[int, bool] | None:
    """«F#m7/C#» -> (6, True); «N» и непонятное -- None."""
    found = CHORD.match(chord or "")
    if not found:
        return None
    root, accidental, minor, extra = found.group(1), found.group(2), found.group(3), found.group(4)
    pc = (NOTE[root] + (1 if accidental == "#" else -1 if accidental == "b" else 0)) % 12
    return pc, bool(minor) or extra == "dim"


def label(pc: int, minor: bool, flats: bool = False) -> str:
    return (FLATS if flats else NAMES)[pc % 12] + ("m" if minor else "")


def _dedup(seq):
    return [x for i, x in enumerate(seq) if i == 0 or x != seq[i - 1]]


def compare(reference: list[str], segments: list[tuple[float, float, str]]) -> dict:
    """Эталон (аккорды по порядку) против разбора [(начало, конец, аккорд)]."""
    ref = _dedup([c for c in (majmin(x) for x in reference) if c])
    flats = any(re.match(r"^[A-H]b", x) for x in reference)
    name = lambda c: label(*c, flats)  # noqa: E731 -- подписи как в эталоне: Bb, а не A#
    ours = [(b - a, c) for a, b, name in segments if (c := majmin(name)) and b > a]
    if not ref or not ours:
        return {"coverage": 0.0, "vocab": 0.0, "transitions": 0.0, "shift": 0, "score": 0.0,
                "found": [], "missed": [name(c) for c in dict.fromkeys(ref)], "extra": []}
    total = sum(d for d, _ in ours)
    best = None
    for shift in range(12):
        moved = [(d, ((pc + shift) % 12, m)) for d, (pc, m) in ours]
        vocab = set(ref)
        coverage = sum(d for d, c in moved if c in vocab) / total
        heard: dict[tuple[int, bool], float] = {}
        for d, c in moved:
            heard[c] = heard.get(c, 0.0) + d
        found = {c for c, d in heard.items() if d >= 2.0}
        vocab_recall = len(vocab & found) / len(vocab)
        ref_pairs = set(zip(ref, ref[1:]))
        seq = _dedup([c for _, c in moved])
        our_pairs = set(zip(seq, seq[1:]))
        transitions = len(ref_pairs & our_pairs) / len(ref_pairs) if ref_pairs else vocab_recall
        score = (coverage + vocab_recall + transitions) / 3
        # без сдвига -- при равенстве: каподастр -- лишь подсказка, не повод «угадать»
        if best is None or score > best["score"] + 1e-9:
            best = {"shift": shift, "coverage": coverage, "vocab": vocab_recall, "transitions": transitions,
                    "score": score, "found": sorted(name(c) for c in vocab & found),
                    "missed": sorted(name(c) for c in vocab - found),
                    "extra": [name(c) for c, d in sorted(heard.items(), key=lambda kv: -kv[1])
                              if c not in vocab and d >= 4.0][:6]}
    plain = compare_shift(ref, ours, 0)
    result = {k: (round(v, 3) if isinstance(v, float) else v) for k, v in best.items()}
    result["unshifted"] = round(plain, 3)
    return result


def compare_shift(ref, ours, shift: int) -> float:
    """Итог при заданном сдвиге -- для честной цифры «как есть»."""
    total = sum(d for d, _ in ours) or 1.0
    moved = [(d, ((pc + shift) % 12, m)) for d, (pc, m) in ours]
    vocab = set(ref)
    coverage = sum(d for d, c in moved if c in vocab) / total
    heard: dict = {}
    for d, c in moved:
        heard[c] = heard.get(c, 0.0) + d
    found = {c for c, d in heard.items() if d >= 2.0}
    vocab_recall = len(vocab & found) / len(vocab)
    ref_pairs = set(zip(ref, ref[1:]))
    seq = _dedup([c for _, c in moved])
    transitions = len(ref_pairs & set(zip(seq, seq[1:]))) / len(ref_pairs) if ref_pairs else vocab_recall
    return (coverage + vocab_recall + transitions) / 3


def run_models(path: str) -> dict[str, list[tuple[float, float, str]]]:
    """Разбор трека каждой моделью, что есть на сервере, -- тем же путём,
    что на сайте: тональность по хромаграмме и подсказка мажор/минор."""
    import librosa

    from midi2tab import audiochords, chordnet

    y, sr = librosa.load(path, sr=chordnet.SR, mono=True)
    harmonic = librosa.effects.harmonic(y, margin=3.0)
    chroma = librosa.feature.chroma_cqt(y=harmonic, sr=sr, bins_per_octave=36)
    key = audiochords.guess_key(chroma)
    out = {}
    for name, model in chordnet.variants().items():
        found = chordnet.detect(y, sr, key=key, chroma=chroma, model=model)
        out[name] = [(round(a, 2), round(b, 2), c) for a, b, c, _ in found]
    return out
