"""Обработчик пробы YuE2-3B на RunPod Serverless.

Вход: style (стиль словами), lyrics (текст с [verse]/[chorus]), seed, cot
(full / melody / off), cfg_scale. Выход: mp3 в base64 прямо в ответе (одна
песня на задачу -- лимит RunPod на размер ответа) и сведения о генерации.
Только для оценки качества: веса под CC BY-NC 4.0.
"""
import base64
import json
import subprocess
import sys
import tempfile
import time

import runpod

_pipe = {}


def pipe():
    if "p" not in _pipe:
        from yue2 import YuE2Pipeline
        started = time.time()
        _pipe["p"] = YuE2Pipeline.from_pretrained("/models/YuE2-3B", vae="/models/YuE2-Vae",
                                                  device="cuda", progress=True)
        print(f"[yue2] модель загружена за {time.time() - started:.0f} с", flush=True)
    return _pipe["p"]


def handler(job):
    started = time.time()
    data = job.get("input") or {}
    try:
        if data.get("mode") == "ping":
            import torch
            return {"ok": True, "cuda": torch.cuda.is_available(),
                    "gpu": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None}
        kwargs = {"cot": data.get("cot") or "full", "seed": int(data.get("seed") or 831001)}
        if data.get("cfg_scale") not in (None, ""):
            kwargs["cfg_scale"] = float(data["cfg_scale"])
        song = pipe()(style=data.get("style") or "", lyrics=data.get("lyrics") or "", **kwargs)
        with tempfile.TemporaryDirectory() as work:
            wav = f"{work}/song.wav"
            song.save(wav)
            mp3 = subprocess.run(["ffmpeg", "-v", "error", "-i", wav, "-b:a", "192k", "-f", "mp3", "pipe:1"],
                                 capture_output=True, check=True).stdout
        return {"ok": True, "files": [{"name": f"yue2_{kwargs['seed']}.mp3", "bytes": len(mp3),
                                       "audio_b64": base64.b64encode(mp3).decode()}],
                "info": {"seconds_audio": round(len(song.audio) / 48000, 1), "timing": json.loads(json.dumps(
                         getattr(song, "timing", None), default=str)), **kwargs},
                "seconds": round(time.time() - started, 1)}
    except Exception as error:  # noqa: BLE001
        print(f"[yue2] {error!r}", file=sys.stderr, flush=True)
        return {"ok": False, "error": str(error)[:1500], "seconds": round(time.time() - started, 1)}


if __name__ == "__main__":
    runpod.serverless.start({"handler": handler})
