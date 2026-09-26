"""Создать или обновить эндпоинт обложек naslux-cover на RunPod (cover/).

    RUNPOD_API_KEY=... COVER_IMAGE=ghcr.io/.../naslux-worker:cover-3 python scripts/runpod_cover.py
"""
import json
import os
import sys

import requests

API = "https://rest.runpod.io/v1"
NAME = "naslux-cover"
IMAGE = os.environ["COVER_IMAGE"]
# SDXL в fp16 занимает ~9 ГБ видеопамяти: хватает самых дешёвых карт на 16 ГБ.
GPUS = ["NVIDIA RTX 2000 Ada Generation", "NVIDIA RTX A4000", "NVIDIA RTX A4500",
        "NVIDIA RTX A5000", "NVIDIA L4"]
headers = {"Authorization": f"Bearer {os.environ['RUNPOD_API_KEY']}"}


def call(method, path, **kwargs):
    response = requests.request(method, f"{API}{path}", headers=headers, timeout=60, **kwargs)
    print(f"{method} {path} -> {response.status_code}\n{response.text[:1200]}\n", flush=True)
    response.raise_for_status()
    return response.json() if response.text else {}


def main() -> None:
    template = next((t for t in call("GET", "/templates") if t.get("name") == NAME), None)
    if template is None:
        template = call("POST", "/templates", json={
            "name": NAME, "imageName": IMAGE, "isServerless": True, "containerDiskInGb": 20})
    else:
        call("PATCH", f"/templates/{template['id']}", json={"imageName": IMAGE})
    endpoint = next((e for e in call("GET", "/endpoints") if e.get("name", "").startswith(NAME)),
                    None)
    if endpoint is None:
        endpoint = call("POST", "/endpoints", json={
            "name": NAME, "templateId": template["id"], "gpuTypeIds": GPUS, "gpuCount": 1,
            "workersMin": 0, "workersMax": 2, "idleTimeout": 30, "flashboot": True,
            "executionTimeoutMs": 300000})
    print("COVER_ENDPOINT", endpoint["id"])


if __name__ == "__main__":
    try:
        main()
    except requests.HTTPError as error:
        sys.exit(f"RunPod API отказал: {error}")
