"""Нарисовать пробные обложки через эндпоинт naslux-cover и сложить их в --out."""
import base64
import os
import sys
import time

import requests

headers = {"Authorization": f"Bearer {os.environ['RUNPOD_API_KEY']}"}
out = sys.argv[1] if len(sys.argv) > 1 else "covers"
os.makedirs(out, exist_ok=True)
endpoints = requests.get("https://rest.runpod.io/v1/endpoints", headers=headers, timeout=60).json()
endpoint = next(e for e in endpoints if e.get("name", "").startswith("naslux-cover"))["id"]
SAMPLES = [
    {"title": "С днём рождения, Саша", "style": "pop rock, upbeat",
     "lyrics": "[Куплет]\nСегодня праздник у тебя\nШары и свечи, все друзья\n[Припев]\nС днём рождения, Саша!"},
    {"title": "Автор - Ночной город.mp3", "style": "nu-metal, heavy guitars, dark",
     "lyrics": "[Verse]\nНеоновые огни горят\nЯ еду в ночь, дороги спят"},
    {"title": "Морской бриз", "style": "acoustic, calm", "lyrics": ""},
]
for number, sample in enumerate(SAMPLES, 1):
    started = time.time()
    job = requests.post(f"https://api.runpod.ai/v2/{endpoint}/runsync", headers=headers,
                        json={"input": {**sample, "seed": number}}, timeout=600).json()
    while job.get("status") in ("IN_QUEUE", "IN_PROGRESS"):
        time.sleep(5)
        job = requests.get(f"https://api.runpod.ai/v2/{endpoint}/status/{job['id']}",
                           headers=headers, timeout=60).json()
    output = job.get("output") or {}
    print(f"[{number}] за {time.time() - started:.0f} с: {job.get('status')}, "
          f"выполнение {job.get('executionTime')} мс, промпт: {output.get('prompt')}", flush=True)
    if output.get("image_b64"):
        with open(os.path.join(out, f"cover_{number}.jpg"), "wb") as f:
            f.write(base64.b64decode(output["image_b64"]))
    else:
        print("   ответ:", str(job)[:800])
