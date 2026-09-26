"""Создать или обновить CPU-эндпоинт ретранслятора Mureka на RunPod.

Ключ Mureka кладётся в переменные окружения шаблона -- в логах его нет:
из ответов RunPod поле env вырезается перед печатью.

    RUNPOD_API_KEY=... MUREKA_API_KEY=... RELAY_IMAGE=ghcr.io/.../naslux-relay:3 \\
        python scripts/runpod_relay.py
"""
import json
import os
import sys

import requests

API = "https://rest.runpod.io/v1"
NAME = "naslux-relay"
IMAGE = os.environ.get("RELAY_IMAGE", "ghcr.io/antonsergeevich/naslux-worker:relay")
headers = {"Authorization": f"Bearer {os.environ['RUNPOD_API_KEY']}"}
CHEAPEST_GPUS = ["NVIDIA RTX 2000 Ada Generation", "NVIDIA RTX A4000", "NVIDIA RTX A4500"]
ENV = {"MUREKA_API_KEY": os.environ["MUREKA_API_KEY"]}


def redact(value):
    if isinstance(value, dict):
        return {k: ("<скрыто>" if k == "env" else redact(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


def call(method, path, **kwargs):
    response = requests.request(method, f"{API}{path}", headers=headers, timeout=60, **kwargs)
    try:
        shown = json.dumps(redact(response.json()), ensure_ascii=False)[:1500]
    except ValueError:
        shown = response.text[:500]
    print(f"{method} {path} -> {response.status_code}\n{shown}\n", flush=True)
    response.raise_for_status()
    return response.json() if response.text else {}


def main() -> None:
    template = next((t for t in call("GET", "/templates") if t.get("name") == NAME), None)
    endpoint = next((e for e in call("GET", "/endpoints") if e.get("name", "").startswith(NAME)),
                    None)
    if endpoint is not None and template is not None and template.get("category") != "CPU":
        # Первый раз шаблон создался в категории NVIDIA -- пересоздаём как CPU.
        call("DELETE", f"/endpoints/{endpoint['id']}")
        endpoint = None
        if template is not None:
            call("DELETE", f"/templates/{template['id']}")
            template = None
    if template is None:
        template = call("POST", "/templates", json={
            "name": NAME, "imageName": IMAGE, "isServerless": True, "category": "CPU",
            "containerDiskInGb": 5, "env": ENV})
    else:
        call("PATCH", f"/templates/{template['id']}", json={"imageName": IMAGE, "env": ENV})
    if endpoint is None:
        endpoint = call("POST", "/endpoints", json={
            "name": NAME, "templateId": template["id"], "computeType": "CPU",
            "cpuFlavorIds": ["cpu3c", "cpu3g", "cpu5c"], "vcpuCount": 2,
            "workersMin": 0, "workersMax": 3, "idleTimeout": 20,
            "executionTimeoutMs": 900000})
    if endpoint.get("gpuTypeIds") and endpoint["gpuTypeIds"] != CHEAPEST_GPUS:
        # REST API RunPod создаёт эндпоинт на видеокарте, даже когда просишь CPU.
        # Ретранслятору хватит самой дешёвой: секунды работы на песню -- копейки.
        endpoint = call("PATCH", f"/endpoints/{endpoint['id']}",
                        json={"gpuTypeIds": CHEAPEST_GPUS, "idleTimeout": 20})
    print("RELAY_ENDPOINT", endpoint["id"], "GPU" if endpoint.get("gpuTypeIds") else "CPU")


if __name__ == "__main__":
    try:
        main()
    except requests.HTTPError as error:
        sys.exit(f"RunPod API отказал: {error}")
