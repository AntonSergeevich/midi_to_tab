"""Обучение нейросети аккордов (CRNN) и честный замер против нынешнего разбора.

Проверка -- на гитаристе GuitarSet, которого модель не слышала (--test-player),
по той же методике MIREX, что scripts/chord_benchmark.py; старый разбор
считается на тех же записях. Модель -- в ONNX (--export) для сайта.

    python train.py --data data --test-player 05 --steps 6000 --export chordnet.onnx
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path

import numpy as np
import torch
from torch import nn

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402

FRAMES = 128  # ~12 секунд на окно обучения


class ChordNet(nn.Module):
    """Свёртки по спектру (локальные созвучия) -> двунаправленный GRU по
    времени (контекст: куда идёт гармония) -> класс на каждый кадр."""

    def __init__(self, hidden: int = 128):
        super().__init__()

        def block(cin, cout, pool):
            return nn.Sequential(nn.Conv2d(cin, cout, 3, padding=1), nn.BatchNorm2d(cout), nn.ReLU(),
                                 nn.Conv2d(cout, cout, 3, padding=1), nn.BatchNorm2d(cout), nn.ReLU(),
                                 nn.MaxPool2d((1, pool)), nn.Dropout(0.1))

        self.conv = nn.Sequential(block(1, 16, 2), block(16, 32, 2), block(32, 48, 3))
        self.proj = nn.Sequential(nn.Linear(48 * 12, 256), nn.ReLU(), nn.Dropout(0.2))
        self.rnn = nn.GRU(256, hidden, batch_first=True, bidirectional=True)
        self.out = nn.Linear(2 * hidden, common.N_CLASS + 1)

    def forward(self, x):                      # x: [B, T, 144]
        h = self.conv(x.unsqueeze(1))          # [B, C, T, 12]
        h = h.permute(0, 2, 1, 3).flatten(2)   # [B, T, C*12]
        h, _ = self.rnn(self.proj(h))
        return self.out(h)                     # логиты [B, T, 25]


def load(folder: str):
    items = []
    for path in sorted(Path(folder).glob("*.npz")):
        data = np.load(path)
        items.append((path.stem, data["x"].astype(np.float32), data["y"].astype(np.int64)))
    return items


def player(name: str) -> str | None:
    """gs_audio_mono-mic_05_Jazz1-200-B_comp_mic -> '05'."""
    import re

    found = re.search(r"_(\d\d)_[A-Za-z]+\d", name) if name.startswith("gs_") else None
    return found.group(1) if found else None


def stem_of(name: str) -> str:
    """gs_<папка>_05_Jazz1-200-B_comp_mic -> 05_Jazz1-200-B_comp_mic."""
    return name[name.index(f"_{player(name)}_") + 1:]


def batch(rng, items, size):
    xs, ys = [], []
    for _ in range(size):
        _, x, y = rng.choice(items)
        if len(x) > FRAMES:
            start = rng.randrange(len(x) - FRAMES)
            x, y = x[start:start + FRAMES], y[start:start + FRAMES]
        # Окно выше на d полутонов (2 полосы на полутон) = звук ниже на d:
        # одна запись даёт все 12 тональностей.
        d = rng.randrange(-6, 6)
        start = common.CENTER + 2 * d
        crop = x[:, start:start + common.WINDOW]
        pad = FRAMES - len(crop)
        xs.append(np.pad(crop, ((0, pad), (0, 0))))
        ys.append(np.pad(common.transpose_class(y, -d), (0, pad), constant_values=common.IGNORE))
    return torch.tensor(np.stack(xs)), torch.tensor(np.stack(ys))


def predict(model, x: np.ndarray) -> np.ndarray:
    crop = x[:, common.CENTER:common.CENTER + common.WINDOW]
    with torch.no_grad():
        logits = model(torch.tensor(crop[None]))[0]
    return torch.softmax(logits, -1).numpy()


def score(reference, estimated) -> dict:
    import mir_eval

    ref_int = np.array([[a, b] for a, b, _ in reference])
    est_int = np.array([[a, b] for a, b, _ in estimated])
    scores = mir_eval.chord.evaluate(ref_int, [l for *_, l in reference],
                                     est_int, [l for *_, l in estimated])
    return {m: float(scores[m]) for m in ("root", "majmin")}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", default="data")
    parser.add_argument("--annotations", default="gs/annotation")
    parser.add_argument("--test-audio", default="gs/audio_mono-mic")
    parser.add_argument("--test-player", default="05")
    parser.add_argument("--steps", type=int, default=6000)
    parser.add_argument("--batch", type=int, default=16)
    parser.add_argument("--synth-share", type=float, default=0.5)
    parser.add_argument("--export", default="chordnet.onnx")
    parser.add_argument("--report", default="report.json")
    parser.add_argument("--baseline", action="store_true", help="считать и старый разбор")
    args = parser.parse_args()
    torch.set_num_threads(max(1, torch.get_num_threads()))
    rng = random.Random(0)
    torch.manual_seed(0)

    items = load(args.data)
    test = [i for i in items if player(i[0]) == args.test_player and i[0].endswith("_comp_mic")]
    guitar = [i for i in items if player(i[0]) not in (None, args.test_player)]
    synth = [i for i in items if i[0].startswith("synth_")]
    print(f"обучение: GuitarSet {len(guitar)}, синтетика {len(synth)}; проверка: {len(test)}", flush=True)

    model = ChordNet()
    optimizer = torch.optim.AdamW(model.parameters(), lr=2e-3, weight_decay=1e-4)
    schedule = torch.optim.lr_scheduler.OneCycleLR(optimizer, max_lr=2e-3, total_steps=args.steps)
    loss_fn = nn.CrossEntropyLoss(ignore_index=common.IGNORE)
    started = time.time()
    model.train()
    for step in range(1, args.steps + 1):
        pool = synth if synth and (not guitar or rng.random() < args.synth_share) else guitar
        x, y = batch(rng, pool, args.batch)
        loss = loss_fn(model(x).reshape(-1, common.N_CLASS + 1), y.reshape(-1))
        optimizer.zero_grad()
        loss.backward()
        nn.utils.clip_grad_norm_(model.parameters(), 1.0)
        optimizer.step()
        schedule.step()
        if step % 250 == 0:
            print(f"шаг {step}/{args.steps}: потери {loss.item():.3f}, {time.time() - started:.0f} с",
                  flush=True)
    model.eval()

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    report = {"steps": args.steps, "train_guitarset": len(guitar), "train_synth": len(synth),
              "files": []}
    penalties = (0.0, 1.0, 2.0, 3.0, 5.0, 8.0)
    totals = {p: {"root": 0.0, "majmin": 0.0} for p in penalties}
    base_total = {"root": 0.0, "majmin": 0.0}
    weight = 0.0
    for name, x, _ in test:
        stem = stem_of(name)
        jams = Path(args.annotations) / (stem.split("_comp_")[0] + "_comp.jams")
        data = json.loads(jams.read_text())
        chords = [a for a in data["annotations"] if a["namespace"] == "chord"][0]["data"]
        reference = [(c["time"], c["time"] + c["duration"], c["value"]) for c in chords]
        length = reference[-1][1]
        probs = predict(model, x)
        row = {"file": stem, "duration": length}
        for p in penalties:
            est = common.segments(common.viterbi(probs, p))
            s = score(reference, est)
            row[f"net_{p}"] = s["majmin"]
            for m in s:
                totals[p][m] += s[m] * length
        if args.baseline:
            from midi2tab import audiochords
            from scripts.chord_benchmark import to_harte

            analysis = audiochords.detect_from_audio(str(Path(args.test_audio) / f"{stem}.wav"))
            est = [(c.start, c.end, to_harte(c.name)) for c in analysis.chords if c.end > c.start] \
                or [(0.0, length, "N")]
            s = score(reference, est)
            row["old"] = s["majmin"]
            for m in s:
                base_total[m] += s[m] * length
        weight += length
        report["files"].append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
    report["net"] = {str(p): {m: v / weight for m, v in t.items()} for p, t in totals.items()}
    if args.baseline:
        report["old"] = {m: v / weight for m, v in base_total.items()}
    best = max(penalties, key=lambda p: totals[p]["majmin"])
    report["best_penalty"] = best
    print("\nИТОГ majmin:", {p: round(t["majmin"] / weight, 3) for p, t in totals.items()},
          "| старый разбор:", round(base_total["majmin"] / weight, 3) if args.baseline else "-",
          flush=True)
    Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=1))

    torch.onnx.export(model, torch.zeros(1, 200, common.WINDOW), args.export,
                      input_names=["cqt"], output_names=["logits"],
                      dynamic_axes={"cqt": {1: "frames"}, "logits": {1: "frames"}}, opset_version=17,
                      dynamo=False)
    print("модель:", args.export, round(Path(args.export).stat().st_size / 1e6, 2), "МБ")


if __name__ == "__main__":
    main()
