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
    """SheetSage2 -- как обычный пакет (/models/SheetSage2 с __init__.py).
    Через trust_remote_code transformers 4.45 копирует в кэш не все её
    модули и падает на chord_spelling_sheetsage2.py (проба 06.10)."""
    sys.path.insert(0, "/models")
    try:
        from SheetSage2.modeling_sheetsage2 import SheetSage2Model
        model = SheetSage2Model.from_pretrained("/models/SheetSage2", local_files_only=True)
    except Exception as error:  # noqa: BLE001 -- запасной путь, как в README
        print(f"[ss] пакетом не вышло ({error!r}), пробуем AutoModel", file=sys.stderr, flush=True)
        model = AutoModel.from_pretrained("/models/SheetSage2", trust_remote_code=True,
                                          local_files_only=True)
    return model.eval().to("cuda")


def read_wav(path):
    """Звук -- массивом [каналы, отсчёты]: читать файлы сама SheetSage2 хочет
    FFmpeg 6.1 с общими библиотеками, а в образе Ubuntu 22.04 -- 4.4."""
    import numpy as np
    from scipy.io import wavfile

    rate, data = wavfile.read(path)
    data = data.astype(np.float32) / (32768.0 if data.dtype == np.int16 else 1.0)
    return (data.T if data.ndim == 2 else data), rate


def main():
    path, melody_only = sys.argv[1], "--melody-only" in sys.argv
    model = load_model()
    waveform, rate = read_wav(path)
    result = model.transcribe(waveform, sampling_rate=rate, melody_only=melody_only)
    labs = result.get("labs") or {}
    print(json.dumps({"abc": result.get("abc") or "", "abc_error": result.get("abc_error"),
                      "chords": rows(labs.get("chord")), "structure": rows(labs.get("structure")),
                      "key": rows(labs.get("key")), "labs": sorted(labs),
                      "duration": result.get("duration_seconds")}, ensure_ascii=False))


if __name__ == "__main__":
    with torch.inference_mode():
        main()
