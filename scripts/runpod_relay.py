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
    if endpoint is not None and endpoint.get("gpuTypeIds"):
        # Первый раз RunPod создал его на видеокарте: шаблон был в категории NVIDIA,
        # и поля CPU он пропустил. Ретранслятору видеокарта не нужна -- пересоздаём.
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
    print("RELAY_ENDPOINT", endpoint["id"], "GPU" if endpoint.get("gpuTypeIds") else "CPU")


if __name__ == "__main__":
    try:
        main()
    except requests.HTTPError as error:
        sys.exit(f"RunPod API отказал: {error}")
