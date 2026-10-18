"""Замер разделения на партии на RunPod: одна песня -- партии тремя способами.

  demucs6s -- как сейчас на сайте: Demucs htdemucs_6s;
  sw       -- BS-RoFormer SW (6 партий: вокал, барабаны, бас, гитара, клавиши, прочее);
  chain    -- «сначала голос»: вокал моделью Resurrection (BS-RoFormer), минус
              (запись без голоса) -- на SW; голос из SW не берём.
Вход: input.audio_b64 (любой формат), input.method. Выход: mp3 каждой партии.
"""
import base64
import glob
import os
import subprocess
import sys
import tempfile
import time

import runpod

SW = "roformer-model-bs-roformer-sw-by-jarredou"
VOCALS = "roformer-model-bs-roformer-vocals-resurrection-by-unwa"


def roformer(model, wav, out):
    folder = tempfile.mkdtemp(dir=os.path.dirname(wav))
    os.symlink(wav, os.path.join(folder, "song.wav"))
    subprocess.run([sys.executable, "-m", "bs_roformer.inference", "--model", model, "--input_folder", folder,
                    "--store_dir", out], check=True, stdout=sys.stderr, stderr=subprocess.STDOUT)
    return {os.path.basename(p)[len("song_"):-4]: p for p in glob.glob(os.path.join(out, "song_*.wav"))}


def mp3(path, bitrate="160k"):
    return subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-b:a", bitrate, "-f", "mp3", "pipe:1"],
                          capture_output=True, check=True).stdout


def separate(method, wav, work):
    if method == "demucs6s":
        out = os.path.join(work, "demucs")
        subprocess.run([sys.executable, "-m", "demucs", "-n", "htdemucs_6s", "-d", "cuda", "-o", out, wav],
                       check=True, stdout=sys.stderr, stderr=subprocess.STDOUT)
        return {os.path.basename(p)[:-4]: p for p in glob.glob(os.path.join(out, "htdemucs_6s", "*", "*.wav"))}
    if method == "sw":
        found = roformer(SW, wav, os.path.join(work, "sw"))
        return {k: v for k, v in found.items() if k != "instrumental"}
    if method == "chain":
        voice = roformer(VOCALS, wav, os.path.join(work, "voc"))
        rest = roformer(SW, voice["instrumental"], os.path.join(work, "rest"))
        parts = {k: v for k, v in rest.items() if k not in ("vocals", "instrumental")}
        parts["vocals"] = voice["vocals"]
        return parts
    raise ValueError(f"неизвестный способ: {method}")


def handler(job):
    data = job.get("input") or {}
    started = time.time()
    try:
        if data.get("mode") == "ping":
            import torch
            return {"ok": True, "cuda": torch.cuda.is_available(),
                    "gpu": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None}
        with tempfile.TemporaryDirectory() as work:
            src = os.path.join(work, "source")
            with open(src, "wb") as f:
                f.write(base64.b64decode(data["audio_b64"]))
            wav = os.path.join(work, "song.wav")
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", src, "-ac", "2", "-ar", "44100", wav], check=True)
            t0 = time.time()
            parts = separate(data.get("method") or "sw", wav, work)
            seconds = round(time.time() - t0, 1)
            files = [{"name": f"{name}.mp3", "audio_b64": base64.b64encode(mp3(path)).decode()}
                     for name, path in sorted(parts.items())]
        return {"ok": True, "method": data.get("method"), "separate_seconds": seconds, "files": files,
                "seconds": round(time.time() - started, 1)}
    except Exception as error:  # noqa: BLE001
        return {"ok": False, "error": str(error)[-1500:]}


runpod.serverless.start({"handler": handler})
