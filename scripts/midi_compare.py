"""Чьё MIDI гитары точнее: Mureka (разделение audio-separation-2, транскрипция)
или наш разбор (Basic Pitch) -- против эталона GuitarSet, где известна
каждая сыгранная нота (гекса-звукосниматель, по струнам).

Метрика -- mir_eval.transcription: нота угадана, если совпала высота и
атака в пределах 50 мс (длительность не важна). F1 -- баланс «ничего не
пропустить» и «ничего не выдумать».

    MUREKA_API_KEY=... python scripts/midi_compare.py --audio gs/audio --annotations gs/annotation \\
        --files 00_Rock3-117-Bb_comp 00_Rock3-117-Bb_solo --out midi_compare
"""
import argparse
import base64
import glob
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from web import studio  # noqa: E402


def reference(jams_path: Path):
    data = json.loads(jams_path.read_text())
    notes = [n for a in data["annotations"] if a["namespace"] == "note_midi" for n in a["data"]]
    intervals = np.array([[n["time"], n["time"] + n["duration"]] for n in notes])
    pitches = np.array([440.0 * 2 ** ((n["value"] - 69) / 12) for n in notes])
    return intervals, pitches


def midi_notes(path: str, skip_drums=True):
    import pretty_midi

    midi = pretty_midi.PrettyMIDI(path)
    notes = [n for i in midi.instruments if not (skip_drums and i.is_drum) for n in i.notes]
    if not notes:
        return np.zeros((0, 2)), np.zeros(0)
    return (np.array([[n.start, n.end] for n in notes]),
            np.array([440.0 * 2 ** ((n.pitch - 69) / 12) for n in notes]))


def score(ref, est) -> dict:
    import mir_eval

    if len(est[1]) == 0:
        return {"precision": 0.0, "recall": 0.0, "f1": 0.0, "notes": 0}
    p, r, f, _ = mir_eval.transcription.precision_recall_f1_overlap(
        ref[0], ref[1], est[0], est[1], onset_tolerance=0.05, offset_ratio=None)
    # И без учёта октавы -- для табов октава решается раскладкой по грифу
    pc = lambda hz: 440.0 * 2 ** (((np.round(12 * np.log2(hz / 440.0)) % 12)) / 12)  # noqa: E731
    _, _, f_pc, _ = mir_eval.transcription.precision_recall_f1_overlap(
        ref[0], pc(ref[1]), est[0], pc(est[1]), onset_tolerance=0.05, offset_ratio=None)
    return {"precision": round(p, 3), "recall": round(r, 3), "f1": round(f, 3),
            "f1_no_octave": round(f_pc, 3), "notes": int(len(est[1]))}


def ours(audio: str, out: str) -> str:
    from midi2tab import audioin

    target = os.path.join(out, "ours.mid")
    audioin.transcribe(audio, target, audioin.TranscribeSettings(min_pitch=40, max_pitch=88))
    return target


def mureka_stem(mp3: str, out: str) -> str | None:
    data = base64.b64encode(Path(mp3).read_bytes()).decode()
    result = studio.mureka_call("POST", "/v1/song/stem", {
        "url": f"data:audio/mp3;base64,{data}", "model": "audio-separation-2"}, timeout=600)
    if not result.get("midi_zip_url"):
        print("   Mureka stem без MIDI:", result, flush=True)
        return None
    studio.download(result["midi_zip_url"], os.path.join(out, "mureka_midi.zip"))
    with zipfile.ZipFile(os.path.join(out, "mureka_midi.zip")) as z:
        z.extractall(os.path.join(out, "mureka_midi"))
    found = sorted(glob.glob(os.path.join(out, "mureka_midi", "**", "*.mid*"), recursive=True))
    print("   MIDI Mureka:", [os.path.basename(f) for f in found], flush=True)
    guitar = [f for f in found if "guitar" in os.path.basename(f).lower()]
    return (guitar or found or [None])[0]


def mureka_transcribe(mp3: str, out: str) -> str | None:
    upload = studio.mureka_upload(mp3, "audio")
    result = studio.mureka_call("POST", "/v1/song/transcribe", {"upload_audio_id": upload}, timeout=600)
    if not result.get("zip_url"):
        print("   транскрипция без файла:", result, flush=True)
        return None
    studio.download(result["zip_url"], os.path.join(out, "transcribe.zip"))
    with zipfile.ZipFile(os.path.join(out, "transcribe.zip")) as z:
        z.extractall(os.path.join(out, "transcribe"))
        print("   транскрипция:", z.namelist(), flush=True)
    xml = sorted(glob.glob(os.path.join(out, "transcribe", "**", "*.*xml"), recursive=True))
    if not xml:
        return None
    import music21

    target = os.path.join(out, "transcribe.mid")
    music21.converter.parse(xml[0]).write("midi", fp=target)
    return target


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audio", required=True)
    parser.add_argument("--annotations", required=True)
    parser.add_argument("--files", nargs="+", required=True)
    parser.add_argument("--out", default="midi_compare")
    parser.add_argument("--skip-mureka", action="store_true")
    args = parser.parse_args()
    report = {}
    for name in args.files:
        out = os.path.join(args.out, name)
        os.makedirs(out, exist_ok=True)
        wav = os.path.join(args.audio, f"{name}_mic.wav")
        mp3 = os.path.join(out, "source.mp3")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", wav, "-b:a", "192k", mp3], check=True)
        ref = reference(Path(args.annotations) / f"{name}.jams")
        row = {"reference_notes": int(len(ref[1]))}
        print(f"{name}: эталон {len(ref[1])} нот", flush=True)
        row["ours"] = score(ref, midi_notes(ours(wav, out)))
        print("   наш разбор:", row["ours"], flush=True)
        if not args.skip_mureka:
            for key, fn in (("mureka_stem", mureka_stem), ("mureka_transcribe", mureka_transcribe)):
                mark = studio.mureka_call("GET", "/v1/account/billing").get("total_spending") or 0
                try:
                    path = fn(mp3, out)
                    row[key] = score(ref, midi_notes(path)) if path else "нет MIDI"
                except Exception as error:  # noqa: BLE001
                    row[key] = f"ошибка: {error}"
                now = studio.mureka_call("GET", "/v1/account/billing").get("total_spending") or 0
                row[f"{key}_cost_usd"] = (now - mark) / 100
                print(f"   {key}: {row[key]}  (списано ${(now - mark) / 100:.2f})", flush=True)
        report[name] = row
    Path(args.out, "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
