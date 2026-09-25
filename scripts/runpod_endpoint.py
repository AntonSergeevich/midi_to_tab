"""Найти или создать serverless-эндпоинт GPU-воркера NASLUX на RunPod.

Эндпоинт с workersMin=0: без запросов воркеров нет и платить не за что --
деньги идут только за секунды работы (и за холодный старт). Если образ
поменялся, шаблон переводится на новый. Все ответы API печатаются целиком:
поля REST API RunPod проверяются прогоном, а не принимаются на веру.

    RUNPOD_API_KEY=... WORKER_IMAGE=ghcr.io/.../naslux-worker:7 python scripts/runpod_endpoint.py
"""
import os
import sys

import requests

API = "https://rest.runpod.io/v1"
NAME = os.environ.get("WORKER_NAME", "naslux-worker")
IMAGE = os.environ.get("WORKER_IMAGE", "ghcr.io/antonsergeevich/naslux-worker:latest")
GPUS = ["NVIDIA RTX A5000", "NVIDIA GeForce RTX 4090", "NVIDIA RTX A4500", "NVIDIA L4"]
# Образ собран под CUDA 12.8: на хосте со старым драйвером torch не видит
# видеокарту и тихо считает на процессоре -- в разы медленнее и дороже.
CUDA = ["12.8", "12.9"]

if not os.environ.get("RUNPOD_API_KEY"):
    sys.exit("Секрет RUNPOD_API_KEY не задан в настройках репозитория "
             "(Settings -> Secrets and variables -> Actions)")
headers = {"Authorization": f"Bearer {os.environ['RUNPOD_API_KEY']}"}


def call(method, path, **kwargs):
    response = requests.request(method, f"{API}{path}", headers=headers, timeout=60, **kwargs)
    print(f"{method} {path} -> {response.status_code}\n{response.text[:2000]}\n")
    response.raise_for_status()
    return response.json() if response.text else {}


def main() -> None:
    template = next((t for t in call("GET", "/templates") if t.get("name") == NAME), None)
    if template is None:
        template = call("POST", "/templates", json={
            "name": NAME, "imageName": IMAGE, "isServerless": True, "containerDiskInGb": 40,
        })
    elif template.get("imageName") != IMAGE:
        call("PATCH", f"/templates/{template['id']}", json={"imageName": IMAGE})

    endpoint = next((e for e in call("GET", "/endpoints") if e.get("name", "").startswith(NAME)),
                    None)
    if endpoint is None:
        endpoint = call("POST", "/endpoints", json={
            "name": NAME, "templateId": template["id"], "gpuTypeIds": GPUS,
            "allowedCudaVersions": CUDA, "workersMin": 0, "workersMax": 1, "idleTimeout": 5,
            "executionTimeoutMs": 2400000, "flashboot": True,
        })
    print("ENDPOINT_ID", endpoint["id"])
    if os.environ.get("GITHUB_ENV"):
        with open(os.environ["GITHUB_ENV"], "a") as env:
            env.write(f"ENDPOINT_ID={endpoint['id']}\n")


if __name__ == "__main__":
    try:
        main()
    except requests.HTTPError as error:
        sys.exit(f"RunPod API отказал: {error}")
