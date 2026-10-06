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


def main():
    path, melody_only = sys.argv[1], "--melody-only" in sys.argv
    model = AutoModel.from_pretrained("/models/SheetSage2", trust_remote_code=True,
                                      local_files_only=True).eval().to("cuda")
    result = model.transcribe(path, melody_only=melody_only)
    labs = result.get("labs") or {}
    print(json.dumps({"abc": result.get("abc") or "", "abc_error": result.get("abc_error"),
                      "chords": rows(labs.get("chord")), "structure": rows(labs.get("structure")),
                      "key": rows(labs.get("key")), "labs": sorted(labs),
                      "duration": result.get("duration_seconds")}, ensure_ascii=False))


if __name__ == "__main__":
    with torch.inference_mode():
        main()
