"""Помогает ли тональность нейросети аккордов? Замер majmin на живых записях.

Сеть -- как на сайте (midi2tab/chordnet), тональность -- как на сайте
(audiochords.guess_key по хромаграмме всей песни). Подсказка тональности --
штраф аккорду, если он вне тональности, а его пара «мажор/минор с тем же
основным тоном» -- в тональности; штраф тем сильнее, чем меньше сеть уверена
в кадре. strength 0 -- без подсказки.

    python eval_prior.py --guitarset gs/audio_mono-mic --annotations gs/annotation --fold 0 \\
        --aam aam_eval --out report.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from midi2tab import audiochords as ac  # noqa: E402
from midi2tab import chordnet  # noqa: E402

STRENGTHS = (0.0, 0.7, 1.5, 2.5, 4.0)


def harte(name: str) -> str:
    if name == "N":
        return "N"
    for suffix, quality in (("maj7", "maj7"), ("m7", "min7"), ("7", "7"), ("m", "min")):
        if name.endswith(suffix) and name[:-len(suffix)] in chordnet.NOTES:
            return f"{name[:-len(suffix)]}:{quality}"
    return f"{name}:maj"


def run(path: str, reference, results: dict, keys: list) -> None:
    import librosa
    import mir_eval

    y, sr = librosa.load(path, sr=chordnet.SR)
    chroma = librosa.feature.chroma_cqt(y=y, sr=sr, hop_length=chordnet.HOP)
    key = ac.guess_key(chroma)
    keys.append(ac.key_name(key))
    probs = chordnet.probabilities(y, sr)
    ref_int = np.array([[a, b] for a, b, _ in reference])
    ref_lab = [l for *_, l in reference]
    for strength in STRENGTHS:
        est = chordnet.decode(chordnet.with_key(probs, key, chroma, strength), 5.0)
        est = [(a, b, harte(n)) for a, b, n, _ in est] or [(0.0, reference[-1][1], "N")]
        s = mir_eval.chord.evaluate(ref_int, ref_lab, np.array([[a, b] for a, b, _ in est]),
                                    [l for *_, l in est])
        row = results.setdefault(str(strength), {"majmin": 0.0, "root": 0.0, "weight": 0.0})
        length = reference[-1][1]
        row["majmin"] += s["majmin"] * length
        row["root"] += s["root"] * length
        row["weight"] += length


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--guitarset", default="")
    parser.add_argument("--annotations", default="")
    parser.add_argument("--fold", type=int, default=0)
    parser.add_argument("--aam", default="")
    parser.add_argument("--out", default="report.json")
    args = parser.parse_args()
    report = {}
    if args.guitarset:
        # Та же разбивка на песни, что в train.py: n-я песня по алфавиту, n % 5 == fold
        audio = sorted(Path(args.guitarset).glob("*_comp_mic.wav"))
        songs = sorted({f.stem.split("_")[1] for f in audio})
        held = {s for n, s in enumerate(songs) if n % 5 == args.fold}
        results, keys = {}, []
        for f in audio:
            if f.stem.split("_")[1] not in held:
                continue
            jams = Path(args.annotations) / (f.name.split("_comp_")[0] + "_comp.jams")
            data = json.loads(jams.read_text())
            chords = [a for a in data["annotations"] if a["namespace"] == "chord"][0]["data"]
            run(str(f), [(c["time"], c["time"] + c["duration"], c["value"]) for c in chords], results, keys)
        report["guitarset"] = {k: {m: v[m] / v["weight"] for m in ("majmin", "root")} for k, v in results.items()}
        print("GuitarSet (неслышанные песни):", report["guitarset"], flush=True)
    if args.aam:
        results, keys = {}, []
        for labels in sorted(Path(args.aam).glob("*.json")):
            audio = next((labels.with_suffix(e) for e in (".flac", ".wav", ".mp3") if labels.with_suffix(e).exists()), None)
            if audio:
                run(str(audio), [tuple(r) for r in json.loads(labels.read_text())], results, keys)
        report["aam"] = {k: {m: v[m] / v["weight"] for m in ("majmin", "root")} for k, v in results.items()}
        print("AAM (полный состав, неслышанный блок):", report["aam"], flush=True)
    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
