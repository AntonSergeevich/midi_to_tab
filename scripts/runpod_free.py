"""Освободить воркеры аккаунта RunPod: у тестовых эндпоинтов (замеры
ACE-Step и разделения, сайт ими не пользуется) workersMax=0. Их «дежурные»
машины занимают лимит воркеров аккаунта -- и рабочему naslux-worker места
не остаётся (29.10: в очереди задача, воркеров 0, хотя видеокарты есть).
Замеры поднимут эндпоинты заново (scripts/runpod_endpoint.py).

    RUNPOD_API_KEY=... python scripts/runpod_free.py
"""
import os
import time

import requests

API = "https://rest.runpod.io/v1"
TESTS = [n.strip() for n in os.environ.get("FREE_NAMES", "naslux-acestep,naslux-sep,vibetrack-acestep").split(",")
         if n.strip()]
WATCH = os.environ.get("WATCH_NAME", "naslux-worker")
headers = {"Authorization": f"Bearer {os.environ['RUNPOD_API_KEY']}"}


def call(method, path, **kwargs):
    response = requests.request(method, f"{API}{path}", headers=headers, timeout=60, **kwargs)
    print(f"{method} {path} -> {response.status_code}\n{response.text[:800]}\n", flush=True)
    return response


endpoints = call("GET", "/endpoints").json()
done = []
for e in endpoints:
    if e.get("name") in TESTS and e.get("workersMax"):
        r = call("PATCH", f"/endpoints/{e['id']}", json={"workersMax": 0, "workersMin": 0})
        done.append(f"{e['name']}: {e.get('workersMax')} -> 0 ({r.status_code})")
print(f"::notice title=Освобождено::{'; '.join(done) or 'тестовые эндпоинты уже без воркеров'}")
watch = next((e for e in endpoints if e.get("name", "").startswith(WATCH)), None)
if watch:
    for _ in range(12):            # до 2 минут: поднимается ли рабочий воркер
        time.sleep(10)
        health = requests.get(f"https://api.runpod.ai/v2/{watch['id']}/health", headers=headers, timeout=30).json()
        workers = health.get("workers") or {}
        if any(workers.get(k) for k in ("initializing", "running", "ready", "idle")):
            break
    print(f"::notice title={WATCH} сейчас::{health}")
