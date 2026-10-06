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


def song(name: str) -> str:
    """gs_..._05_Jazz1-200-B_comp_mic -> Jazz1-200-B (одна и та же песня у всех гитаристов)."""
    return stem_of(name).split("_")[1]


def stem_of(name: str) -> str:
    """gs_<папка>_05_Jazz1-200-B_comp_mic -> 05_Jazz1-200-B_comp_mic."""
    return name[name.index(f"_{player(name)}_") + 1:]


def batch(rng, items, size, augment: bool = False):
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
    x = np.stack(xs)
    if augment:
        x = spec_augment(rng, x)
    return torch.tensor(x), torch.tensor(np.stack(ys))


def spec_augment(rng, x: np.ndarray) -> np.ndarray:
    """Другой тембр и запись: наклон спектра (ярче/глуше), громкость, шум,
    выпавшие кусочки времени. Полосы частот не маскируем -- терца решает
    мажор/минор, её прятать нельзя."""
    x = x.copy()
    bins = np.linspace(-1.0, 1.0, x.shape[2], dtype=np.float32)
    for b in range(len(x)):
        x[b] = x[b] * rng.uniform(0.8, 1.2) + rng.gauss(0, 0.3) * bins + rng.gauss(0, 0.1)
        x[b] += np.random.default_rng(rng.randrange(1 << 30)).normal(0, rng.uniform(0, 0.15),
                                                                     x[b].shape).astype(np.float32)
        for _ in range(rng.randrange(3)):
            width = rng.randrange(1, 8)
            start = rng.randrange(max(1, x.shape[1] - width))
            x[b, start:start + width] = 0.0
    return x


def class_weights(pools) -> torch.Tensor:
    """Веса классов по качеству аккорда: минора и септаккордов в данных
    меньше мажора, и без весов сеть в сомнении отвечает «мажор» (Em -> E).
    Вес ~ 1/sqrt(частоты качества), в пределах 0.5..3."""
    counts = np.zeros(len(common.QUALITIES) + 1)
    for items, share in pools:
        if not items or share <= 0:
            continue
        part = np.zeros_like(counts)
        for _, _, y in items:
            chord = y[(y >= 0) & (y < common.N_CLASS)]
            part[:-1] += np.bincount(chord // 12, minlength=len(common.QUALITIES))
            part[-1] += np.sum(y == common.N_CLASS)
        counts += share * part / max(part.sum(), 1)
    freq = counts / counts.sum()
    per_quality = np.clip((freq.mean() / np.maximum(freq, 1e-6)) ** 0.5, 0.5, 3.0)
    weights = np.concatenate([np.repeat(per_quality[:-1], 12), per_quality[-1:]])
    print("доли качеств:", dict(zip(common.QUALITIES + ["N"], np.round(freq, 3))),
          "| веса:", dict(zip(common.QUALITIES + ["N"], np.round(per_quality, 2))), flush=True)
    return torch.tensor(weights, dtype=torch.float32)


def reference_from_frames(y: np.ndarray):
    """Покадровая разметка -> отрезки для mir_eval (вне словаря -- «X»)."""
    step = common.HOP / common.SR
    out = []
    for t, c in enumerate(y):
        label = "X" if c < 0 else common.CLASSES[int(c)]
        if out and out[-1][2] == label:
            out[-1][1] = (t + 1) * step
        else:
            out.append([t * step, (t + 1) * step, label])
    return [tuple(o) for o in out]


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
    return {m: float(scores[m]) for m in ("root", "majmin", "sevenths", "mirex")}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", default="data")
    parser.add_argument("--annotations", default="gs/annotation")
    parser.add_argument("--test-audio", default="gs/audio_mono-mic")
    parser.add_argument("--test-player", default="05")
    parser.add_argument("--split", default="songs", choices=["songs", "player"],
                        help="songs -- проверка на песнях, которых модель не слышала ни у кого "
                             "(честнее: в GuitarSet все гитаристы играют одни и те же 30 песен)")
    parser.add_argument("--fold", type=int, default=0, help="какая пятая часть песен -- проверочная")
    parser.add_argument("--steps", type=int, default=6000)
    parser.add_argument("--batch", type=int, default=16)
    parser.add_argument("--synth-share", type=float, default=0.5)
    parser.add_argument("--aam-share", type=float, default=0.0, help="доля шагов на AAM")
    parser.add_argument("--fma-share", type=float, default=0.0,
                        help="доля шагов на живые песни FMA с разметкой учителя (pseudo.py)")
    parser.add_argument("--weights", action="store_true", help="веса классов по качеству аккорда")
    parser.add_argument("--augment", action="store_true", help="аугментации спектра")
    parser.add_argument("--smoothing", type=float, default=0.0, help="сглаживание меток")
    parser.add_argument("--hidden", type=int, default=128, help="ширина рекуррентного слоя (128 -- как на сайте)")
    parser.add_argument("--export", default="chordnet.onnx")
    parser.add_argument("--report", default="report.json")
    parser.add_argument("--baseline", action="store_true", help="считать и старый разбор")
    args = parser.parse_args()
    torch.set_num_threads(max(1, torch.get_num_threads()))
    rng = random.Random(0)
    torch.manual_seed(0)

    items = load(args.data)
    if args.split == "player":
        held = lambda name: player(name) == args.test_player  # noqa: E731
    else:
        songs = sorted({song(i[0]) for i in items if player(i[0])})
        test_songs = {s for n, s in enumerate(songs) if n % 5 == args.fold}
        held = lambda name: song(name) in test_songs  # noqa: E731
        print("проверочные песни:", sorted(test_songs), flush=True)
    test = [i for i in items if player(i[0]) and held(i[0]) and i[0].endswith("_comp_mic")]
    guitar = [i for i in items if player(i[0]) and not held(i[0])]
    # synth_* и synth1_*..synth4_* (параллельная генерация): раньше второе не
    # попадало в обучение -- синтетика молча выпадала
    synth = [i for i in items if i[0].startswith("synth")]
    aam_all = [i for i in items if i[0].startswith("aam_")]
    # Каждая десятая песня AAM (до 60) -- проверочная, модель её не слышит
    aam_test = aam_all[::10][:60]
    held_aam = {i[0] for i in aam_test}
    aam = [i for i in aam_all if i[0] not in held_aam]
    fma = [i for i in items if i[0].startswith("fma_")]
    print(f"обучение: GuitarSet {len(guitar)}, синтетика {len(synth)}, AAM {len(aam)}, FMA {len(fma)}; "
          f"проверка: GuitarSet {len(test)}, AAM {len(aam_test)}", flush=True)
    guitar_share = max(0.0, 1.0 - args.synth_share - args.aam_share - args.fma_share)
    pools = [(aam, args.aam_share if aam else 0.0), (synth, args.synth_share if synth else 0.0),
             (fma, args.fma_share if fma else 0.0), (guitar, guitar_share if guitar else 0.0)]
    total_share = sum(share for _, share in pools) or 1.0

    model = ChordNet(hidden=args.hidden)
    optimizer = torch.optim.AdamW(model.parameters(), lr=2e-3, weight_decay=1e-4)
    schedule = torch.optim.lr_scheduler.OneCycleLR(optimizer, max_lr=2e-3, total_steps=args.steps)
    loss_fn = nn.CrossEntropyLoss(ignore_index=common.IGNORE, label_smoothing=args.smoothing,
                                  weight=class_weights(pools) if args.weights else None)
    started = time.time()
    model.train()
    for step in range(1, args.steps + 1):
        r, pool = rng.random() * total_share, guitar
        for items_, share in pools:
            if share > 0 and r < share:
                pool = items_
                break
            r -= share
        x, y = batch(rng, pool, args.batch, augment=args.augment)
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
              "train_aam": len(aam), "train_fma": len(fma), "fma_share": args.fma_share, "weights": args.weights, "augment": args.augment,
              "smoothing": args.smoothing, "files": []}
    penalties = (0.0, 1.0, 2.0, 3.0, 5.0, 8.0)
    totals = {p: {"root": 0.0, "majmin": 0.0, "sevenths": 0.0, "mirex": 0.0} for p in penalties}
    base_total = {"root": 0.0, "majmin": 0.0, "sevenths": 0.0, "mirex": 0.0}
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
    weight = weight or 1.0   # без проверки на GuitarSet (например, быстрый прогон) -- нули
    report["net"] = {str(p): {m: v / weight for m, v in t.items()} for p, t in totals.items()}
    if args.baseline:
        report["old"] = {m: v / weight for m, v in base_total.items()}
    if aam_test:
        aam_totals = {p: {"root": 0.0, "majmin": 0.0, "sevenths": 0.0, "mirex": 0.0} for p in penalties}
        aam_weight = 0.0
        for name, x, y in aam_test:
            reference = reference_from_frames(y)
            length = reference[-1][1]
            probs = predict(model, x)
            for p in penalties:
                s = score(reference, common.segments(common.viterbi(probs, p)))
                for m in s:
                    aam_totals[p][m] += s[m] * length
            aam_weight += length
        report["aam"] = {str(p): {m: v / aam_weight for m, v in t.items()} for p, t in aam_totals.items()}
        print("\nИТОГ AAM majmin:", {p: round(t["majmin"] / aam_weight, 3) for p, t in aam_totals.items()},
              flush=True)
    best = max(penalties, key=lambda p: totals[p]["majmin"])
    report["best_penalty"] = best
    for metric in ("majmin", "sevenths", "mirex"):
        print(f"\nИТОГ {metric}:", {p: round(t[metric] / weight, 3) for p, t in totals.items()},
              "| старый разбор:", round(base_total[metric] / weight, 3) if args.baseline else "-",
              flush=True)
    Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=1))

    torch.onnx.export(model, torch.zeros(1, 200, common.WINDOW), args.export,
                      input_names=["cqt"], output_names=["logits"],
                      dynamic_axes={"cqt": {1: "frames"}, "logits": {1: "frames"}}, opset_version=17,
                      dynamo=False)
    print("модель:", args.export, round(Path(args.export).stat().st_size / 1e6, 2), "МБ")


if __name__ == "__main__":
    main()
