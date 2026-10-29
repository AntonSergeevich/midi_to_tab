"""Расширить выбор машин у эндпоинта RunPod: больше типов видеокарт и версий
CUDA, если задачи стоят в очереди без воркеров («Ждём свободную видеокарту»).

    RUNPOD_API_KEY=... TUNE_NAME=naslux-worker python scripts/runpod_tune.py

Драйвер с CUDA 13 запускает образы, собранные под 12.8: торч видит видеокарту.
Все ответы API печатаются целиком."""
import json
import os
import sys

import requests

API = "https://rest.runpod.io/v1"
NAME = os.environ.get("TUNE_NAME", "naslux-worker")
GPUS = [g.strip() for g in os.environ.get("TUNE_GPUS", "").split(",") if g.strip()] or [
    "NVIDIA RTX A5000", "NVIDIA GeForce RTX 4090", "NVIDIA RTX A4500", "NVIDIA L4",
    "NVIDIA GeForce RTX 3090", "NVIDIA RTX A6000", "NVIDIA A40", "NVIDIA RTX 4000 Ada Generation",
    "NVIDIA L40S", "NVIDIA RTX 6000 Ada Generation"]
CUDA = [c.strip() for c in os.environ.get("TUNE_CUDA", "12.8,12.9,13.0").split(",") if c.strip()]
headers = {"Authorization": f"Bearer {os.environ['RUNPOD_API_KEY']}"}


def call(method, path, **kwargs):
    response = requests.request(method, f"{API}{path}", headers=headers, timeout=60, **kwargs)
    print(f"{method} {path} -> {response.status_code}\n{response.text[:1500]}\n", flush=True)
    return response


endpoint = next((e for e in call("GET", "/endpoints").json() if e.get("name", "").startswith(NAME)), None)
if not endpoint:
    sys.exit(f"нет эндпоинта {NAME}")
body = {"gpuTypeIds": GPUS, "allowedCudaVersions": CUDA}
r = call("PATCH", f"/endpoints/{endpoint['id']}", json=body)
if r.status_code >= 400 and "13.0" in CUDA:
    # версию CUDA 13 API может ещё не знать -- хотя бы видеокарты
    print("без CUDA 13:", json.dumps(body), flush=True)
    r = call("PATCH", f"/endpoints/{endpoint['id']}", json={"gpuTypeIds": GPUS, "allowedCudaVersions": ["12.8", "12.9"]})
r.raise_for_status()
after = r.json()
print(f"::notice title={NAME}::видеокарты {after.get('gpuTypeIds')} · CUDA {after.get('allowedCudaVersions')}")
h = requests.get(f"https://api.runpod.ai/v2/{endpoint['id']}/health", headers=headers, timeout=30).text[:400]
print(f"::notice title={NAME} сейчас::{h}")
