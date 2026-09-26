"""Синтетические записи с точной разметкой аккордов.

Случайная, но музыкально правдоподобная гармония (ступени тональности,
иногда чужие аккорды), разные инструменты General MIDI, фактура (держать
аккорд, бой, арпеджио), обращения, септаккорды, бас, мелодия с проходящими
нотами поверх, барабаны. Звук -- FluidSynth по свободным звуковым банкам.

    python synth.py --count 1500 --out synth/ --soundfonts /usr/share/sounds/sf2/*.sf2
"""
from __future__ import annotations

import argparse
import json
import os
import random
import subprocess
from pathlib import Path

import pretty_midi

NOTES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
MAJOR = [(0, "maj", 3), (2, "min", 2), (4, "min", 1), (5, "maj", 3), (7, "maj", 3), (9, "min", 3)]
MINOR = [(0, "min", 3), (3, "maj", 2), (5, "min", 2), (7, "min", 1), (7, "maj", 2),
         (8, "maj", 3), (10, "maj", 3)]
SCALE = {"maj": [0, 2, 4, 5, 7, 9, 11], "min": [0, 2, 3, 5, 7, 8, 10]}
HARMONY = [0, 1, 2, 4, 5, 16, 18, 19, 21, 24, 25, 26, 27, 28, 29, 30, 46, 48, 49, 50, 52, 61,
           62, 81, 88, 89, 90, 92, 94]
BASS = [32, 33, 34, 35, 38, 39]
LEAD = [52, 53, 56, 60, 64, 65, 66, 71, 73, 74, 80, 81]


def chord_tones(root: int, quality: str, extra: str) -> list[int]:
    third = 4 if quality == "maj" else 3
    tones = [0, third, 7]
    if extra == "7":
        tones.append(10)
    elif extra == "maj7":
        tones.append(11 if quality == "maj" else 10)
    elif extra == "sus4":
        tones = [0, 5, 7]
    elif extra == "5":
        tones = [0, 7]
    elif extra == "add9":
        tones.append(14)
    return [(root + t) % 12 for t in tones]


def progression(rng: random.Random, key: int, mode: str, seconds: float, beat: float):
    """[(начало, конец, корень, качество, добавка)] на seconds секунд."""
    degrees = MAJOR if mode == "maj" else MINOR
    out, t = [], 0.0
    while t < seconds:
        if rng.random() < 0.1:
            root, quality = rng.randrange(12), rng.choice(["maj", "min"])  # чужой аккорд
        else:
            step, quality, _ = rng.choices(degrees, weights=[d[2] for d in degrees])[0]
            root = (key + step) % 12
        extra = rng.choices(["", "7", "maj7", "sus4", "5", "add9"],
                            weights=[60, 18, 8, 5, 5, 4])[0]
        if rng.random() < 0.04:
            quality = "N"
        length = rng.choice([1, 2, 2, 4, 4, 4, 4, 8, 8]) * beat
        out.append((t, min(seconds, t + length), root, quality, extra))
        t += length
    return out


def label(root: int, quality: str, extra: str) -> str:
    if quality == "N":
        return "N"
    if extra in ("sus4", "5"):
        return "X"  # вне словаря maj/min: не учим
    return f"{NOTES[root]}:{quality}"


def voicing(rng: random.Random, tones: list[int], low: int) -> list[int]:
    """Аккорд в регистре: случайное обращение, иногда «гитарная» раскладка."""
    start = rng.randrange(len(tones)) if rng.random() < 0.35 else 0
    ordered = tones[start:] + tones[:start]
    pitches, pitch = [], low + (ordered[0] - low) % 12
    for tone in ordered + ([ordered[0]] if rng.random() < 0.5 else []):
        while pitch % 12 != tone:
            pitch += 1
        pitches.append(pitch)
        pitch += rng.choice([1, 3, 5]) if len(pitches) > 3 else 1
    return pitches


def play_harmony(rng, inst, chords, beat):
    style = rng.choice(["block", "strum", "strum", "arp", "offbeat"])
    low = rng.randrange(45, 60)
    velocity = rng.randrange(55, 110)
    for start, end, root, quality, extra in chords:
        if quality == "N":
            continue
        pitches = voicing(rng, chord_tones(root, quality, extra), low)
        if style == "block":
            hits = [(start, end)]
        elif style == "offbeat":
            hits = [(t + beat / 2, min(end, t + beat)) for t in _grid(start, end, beat)]
        else:
            step = beat / rng.choice([1, 2, 2, 4]) if style == "strum" else beat / rng.choice([2, 4])
            hits = [(t, min(end, t + step)) for t in _grid(start, end, step)]
        for number, (a, b) in enumerate(hits):
            if style == "arp":
                note_pitches = [pitches[number % len(pitches)]]
            else:
                note_pitches = pitches[::-1] if (number % 2 and style == "strum") else pitches
            for k, pitch in enumerate(note_pitches):
                delay = k * rng.uniform(0.005, 0.02) if style == "strum" else 0.0
                v = max(1, min(127, velocity + rng.randrange(-12, 12)))
                inst.notes.append(pretty_midi.Note(v, pitch, a + delay, max(a + delay + 0.05, b)))


def _grid(start, end, step):
    t = start
    while t < end - 1e-6:
        yield t
        t += step


def play_bass(rng, inst, chords, beat):
    step = beat * rng.choice([1, 1, 2, 0.5])
    for start, end, root, quality, extra in chords:
        if quality == "N":
            continue
        base = 36 + root if rng.random() < 0.85 else 36 + (root + (4 if quality == "maj" else 3)) % 12
        for n, t in enumerate(_grid(start, end, step)):
            pitch = base + (7 if n % 4 == 2 and rng.random() < 0.3 else 0)
            inst.notes.append(pretty_midi.Note(rng.randrange(70, 110), pitch, t, min(end, t + step * 0.9)))


def play_melody(rng, inst, chords, beat, key, mode):
    scale = [(key + s) % 12 for s in SCALE["maj" if mode == "maj" else "min"]]
    pitch = rng.randrange(64, 76)
    for start, end, root, quality, extra in chords:
        tones = chord_tones(root, "maj" if quality == "N" else quality, extra)
        for n, t in enumerate(_grid(start, end, beat / rng.choice([1, 2]))):
            if rng.random() < 0.25:
                continue  # пауза
            pool = tones if n % 2 == 0 else scale  # на сильных долях -- звуки аккорда
            target = rng.choice(pool)
            candidates = [p for p in range(pitch - 7, pitch + 8) if p % 12 == target and 57 <= p <= 88]
            pitch = rng.choice(candidates) if candidates else pitch
            inst.notes.append(pretty_midi.Note(rng.randrange(60, 105), pitch, t, t + beat * 0.45))


def play_drums(rng, inst, seconds, beat):
    for n, t in enumerate(_grid(0, seconds, beat / 2)):
        if n % 2 == 0:
            inst.notes.append(pretty_midi.Note(90, 42, t, t + 0.05))
        if n % 8 in (0, 5) or (n % 8 == 3 and rng.random() < 0.3):
            inst.notes.append(pretty_midi.Note(100, 36, t, t + 0.1))
        if n % 8 in (2, 6):
            inst.notes.append(pretty_midi.Note(95, 38, t, t + 0.1))


def make(rng: random.Random, seconds: float):
    key, mode = rng.randrange(12), rng.choice(["maj", "maj", "min"])
    bpm = rng.uniform(60, 180)
    beat = 60.0 / bpm
    chords = progression(rng, key, mode, seconds, beat)
    midi = pretty_midi.PrettyMIDI(initial_tempo=bpm)
    for _ in range(rng.choice([1, 1, 2, 2, 3])):
        inst = pretty_midi.Instrument(program=rng.choice(HARMONY))
        play_harmony(rng, inst, chords, beat)
        midi.instruments.append(inst)
    if rng.random() < 0.8:
        inst = pretty_midi.Instrument(program=rng.choice(BASS))
        play_bass(rng, inst, chords, beat)
        midi.instruments.append(inst)
    if rng.random() < 0.6:
        inst = pretty_midi.Instrument(program=rng.choice(LEAD))
        play_melody(rng, inst, chords, beat, key, mode)
        midi.instruments.append(inst)
    if rng.random() < 0.7:
        inst = pretty_midi.Instrument(program=0, is_drum=True)
        play_drums(rng, inst, seconds, beat)
        midi.instruments.append(inst)
    labels = [(a, b, label(r, q, e)) for a, b, r, q, e in chords]
    return midi, labels


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=1000)
    parser.add_argument("--seconds", type=float, default=30.0)
    parser.add_argument("--out", default="synth")
    parser.add_argument("--soundfonts", nargs="+", required=True)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--prefix", default="synth", help="начало имён (для параллельных запусков)")
    args = parser.parse_args()
    os.makedirs(args.out, exist_ok=True)
    rng = random.Random(args.seed)
    for number in range(args.count):
        midi, labels = make(rng, args.seconds)
        base = Path(args.out) / f"{args.prefix}_{number:05d}"
        midi.write(f"{base}.mid")
        subprocess.run(["fluidsynth", "-ni", "-g", "0.6", "-F", f"{base}.wav", "-r", "22050",
                        rng.choice(args.soundfonts), f"{base}.mid"],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        os.remove(f"{base}.mid")
        Path(f"{base}.json").write_text(json.dumps(labels))
        if number % 100 == 0:
            print(f"синтетика: {number}/{args.count}", flush=True)


if __name__ == "__main__":
    main()
