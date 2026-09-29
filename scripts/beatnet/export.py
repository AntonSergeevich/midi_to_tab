"""Beat This (CPJKU, MIT) -> ONNX для сайта: midi2tab/models/beatthis.onnx.

Эталон считаем ДО экспорта: трассировка пишет мусор в кеш rotary-
эмбеддингов, и та же модель после экспорта в этом процессе врёт.
"""

import argparse
from pathlib import Path

import numpy as np
import onnxruntime as ort
import torch
from beat_this.inference import load_model


class Outputs(torch.nn.Module):
    def __init__(self, model):
        super().__init__()
        self.model = model

    def forward(self, spect):
        out = self.model(spect)
        return out["beat"], out["downbeat"]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--checkpoints", nargs="+", default=["final0", "small0"])
    parser.add_argument("--out", default="beat_models")
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    torch.manual_seed(0)
    probes = [torch.randn(1, n, 128) * 2 + 3 for n in (1500, 777)]
    for name in args.checkpoints:
        model = Outputs(load_model(name, "cpu")).eval()
        with torch.no_grad():
            refs = [model(p) for p in probes]
        target = out / f"beatthis_{name}.onnx"
        torch.onnx.export(model, probes[0], str(target), input_names=["spect"], output_names=["beat", "downbeat"],
                          dynamic_axes={"spect": {1: "time"}, "beat": {1: "time"}, "downbeat": {1: "time"}},
                          opset_version=17, dynamo=False)
        session = ort.InferenceSession(str(target), providers=["CPUExecutionProvider"])
        worst = max(float(np.abs(o - r.numpy()).max())
                    for p, ref in zip(probes, refs)
                    for o, r in zip(session.run(None, {"spect": p.numpy()}), ref))
        params = sum(x.numel() for x in model.parameters()) / 1e6
        print(f"{name}: {params:.1f} млн параметров, {target.stat().st_size / 1e6:.1f} МБ, "
              f"расхождение с torch {worst:.2e}", flush=True)
        if worst > 1e-3:
            raise SystemExit(f"{name}: ONNX расходится с torch")


if __name__ == "__main__":
    main()
