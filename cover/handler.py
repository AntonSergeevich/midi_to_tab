"""Обложки NASLUX: картинка к песне по названию, стилю и первым строкам текста.

SDXL-Lightning (ByteDance, лицензия openrail++ -- коммерческое использование
разрешено) рисует 1024x1024 за 4 шага, около секунды на видеокарте. Русский
текст переводится на английский офлайн (Argos Translate): SDXL понимает
только английский.

Вход: {"title", "lyrics", "style", "seed", "upload_url"}. Если upload_url
задан -- cover.jpg уходит туда POST'ом (заголовок X-File-Name), иначе
приходит в ответе base64 (для проверки).
"""
import base64
import io
import re
import urllib.parse
import urllib.request

import runpod
import torch
from diffusers import EulerDiscreteScheduler, StableDiffusionXLPipeline, UNet2DConditionModel
from diffusers.models import AutoencoderKL
from safetensors.torch import load_file

BASE = "/models/sdxl"
UNET = "/models/sdxl_lightning_4step_unet.safetensors"
VAE = "/models/vae"

unet = UNet2DConditionModel.from_config(BASE, subfolder="unet").to("cuda", torch.float16)
unet.load_state_dict(load_file(UNET, device="cuda"))
pipe = StableDiffusionXLPipeline.from_pretrained(
    BASE, unet=unet, vae=AutoencoderKL.from_pretrained(VAE, torch_dtype=torch.float16),
    torch_dtype=torch.float16, variant="fp16").to("cuda")
pipe.scheduler = EulerDiscreteScheduler.from_config(pipe.scheduler.config,
                                                    timestep_spacing="trailing")
pipe.set_progress_bar_config(disable=True)

_translate = None


def to_english(text: str) -> str:
    global _translate
    if not re.search("[а-яё]", text, re.I):
        return text
    if _translate is None:
        import argostranslate.translate
        _translate = argostranslate.translate
    return _translate.translate(text, "ru", "en")


def imagery(lyrics: str) -> str:
    """Первые содержательные строки текста: без [Куплет], [Припев] и пустых."""
    lines = [l.strip() for l in (lyrics or "").splitlines()
             if l.strip() and not l.strip().startswith(("[", "("))]
    return " ".join(lines[:3])[:300]


def build_prompt(job: dict) -> str:
    title = re.sub(r"\.(mp3|wav|flac|ogg|m4a|aiff?)$", "", job.get("title", ""), flags=re.I)
    title = re.sub(r"^[^-–]{1,60}\s[-–]\s", "", title)  # «Исполнитель - Песня» -> «Песня»
    parts = [to_english(p) for p in (title, imagery(job.get("lyrics", ""))) if p.strip()]
    style = (job.get("style") or "")[:200]
    return ("album cover art, " + ", ".join(parts) + (f", {style} music" if style else "")
            + ", cinematic lighting, rich colors, highly detailed digital painting, "
              "centered composition, no text")


def handler(event):
    job = event.get("input") or {}
    prompt = build_prompt(job)
    generator = torch.Generator("cuda").manual_seed(int(job.get("seed") or 0) % 2**31)
    image = pipe(prompt, num_inference_steps=4, guidance_scale=0, width=1024, height=1024,
                 generator=generator).images[0]
    buffer = io.BytesIO()
    image.resize((640, 640)).save(buffer, "JPEG", quality=88)
    data = buffer.getvalue()
    upload = job.get("upload_url")
    if upload:
        req = urllib.request.Request(upload, data=data, method="POST", headers={
            "Content-Type": "image/jpeg", "X-File-Name": urllib.parse.quote("cover.jpg")})
        with urllib.request.urlopen(req, timeout=120) as resp:
            resp.read()
        return {"ok": True, "prompt": prompt, "bytes": len(data)}
    return {"ok": True, "prompt": prompt, "image_b64": base64.b64encode(data).decode()}


if __name__ == "__main__":
    runpod.serverless.start({"handler": handler})
