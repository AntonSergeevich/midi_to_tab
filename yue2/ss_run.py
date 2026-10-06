"""SheetSage2 в своём окружении (/venv-ss): запись -> JSON на stdout.

    /venv-ss/bin/python ss_run.py song.wav [--melody-only]

Выход: abc (ноты; без аккордов при --melody-only), chords/structure/key --
отрезки [начало, конец, метка] из LAB-файлов, длительность.
"""
import json
import sys

import torch
from transformers import AutoModel


def rows(text):
    out = []
    for line in (text or "").splitlines():
        parts = line.split(None, 2)
        if len(parts) == 3:
            try:
                out.append([round(float(parts[0]), 2), round(float(parts[1]), 2), parts[2].strip()])
            except ValueError:
                continue
    return out


def load_model():
    """SheetSage2 -- сначала как обычный пакет (/models/SheetSage2 с
    __init__.py), иначе через AutoModel. transformers 4.45 при
    trust_remote_code копирует в свой кэш модулей не все файлы SheetSage2 и
    падает на chord_spelling_sheetsage2.py (пробы 06.10) -- поэтому кэш
    модулей заполняем сами: все .py модели лежат там заранее."""
    import os
    import shutil

    source = "/models/SheetSage2"
    cache = os.path.join(os.environ.get("HF_HOME", "/models/hf"), "modules", "transformers_modules", "SheetSage2")
    os.makedirs(cache, exist_ok=True)
    for name in os.listdir(source):
        if name.endswith(".py"):
            shutil.copy2(os.path.join(source, name), os.path.join(cache, name))
    errors = []
    sys.path.insert(0, "/models")
    try:
        from SheetSage2.modeling_sheetsage2 import SheetSage2Model
        model = SheetSage2Model.from_pretrained(source, local_files_only=True)
    except Exception as error:  # noqa: BLE001 -- запасной путь, как в README
        errors.append(f"пакетом: {error!r}"[:400])
        print(f"[ss] {errors[-1]}", file=sys.stderr, flush=True)
        try:
            model = AutoModel.from_pretrained(source, trust_remote_code=True, local_files_only=True)
        except Exception as second:  # noqa: BLE001
            raise RuntimeError(" || ".join(errors + [f"AutoModel: {second!r}"[:600]])) from second
    return model.eval().to("cuda")


def read_wav(path):
    """Звук -- массивом [каналы, отсчёты]: читать файлы сама SheetSage2 хочет
    FFmpeg 6.1 с общими библиотеками, а в образе Ubuntu 22.04 -- 4.4."""
    import numpy as np
    from scipy.io import wavfile

    rate, data = wavfile.read(path)
    data = data.astype(np.float32) / (32768.0 if data.dtype == np.int16 else 1.0)
    return (data.T if data.ndim == 2 else data), rate


def save_midis(result, out):
    """MIDI частей -- файлами в папку out: мелодия вокала, мелодия
    инструментов, аккорды и всё вместе."""
    import os

    os.makedirs(out, exist_ok=True)
    saved = []
    parts = dict(result.get("midis") or {})
    if result.get("midi"):
        parts["transcription"] = result["midi"]
    for name, data in parts.items():
        if isinstance(data, (bytes, bytearray)) and data:
            safe = "".join(c if c.isalnum() or c in "_-" else "_" for c in str(name).rsplit(".", 1)[0])
            with open(os.path.join(out, f"{safe}.mid"), "wb") as f:
                f.write(data)
            saved.append(f"{safe}.mid")
    return saved


def main():
    path, melody_only = sys.argv[1], "--melody-only" in sys.argv
    out = sys.argv[sys.argv.index("--out") + 1] if "--out" in sys.argv else None
    model = load_model()
    waveform, rate = read_wav(path)
    result = model.transcribe(waveform, sampling_rate=rate, melody_only=melody_only)
    labs = result.get("labs") or {}
    print(json.dumps({"abc": result.get("abc") or "", "abc_error": result.get("abc_error"),
                      "chords": rows(labs.get("chord")), "structure": rows(labs.get("structure")),
                      "key": rows(labs.get("key")), "labs": sorted(labs),
                      "midis": save_midis(result, out) if out else [],
                      "duration": result.get("duration_seconds")}, ensure_ascii=False))


if __name__ == "__main__":
    with torch.inference_mode():
        main()
