"""Проверить ретранслятор: через RunPod спросить у Mureka баланс."""
import os
import sys
import time

import requests

headers = {"Authorization": f"Bearer {os.environ['RUNPOD_API_KEY']}"}
endpoints = requests.get("https://rest.runpod.io/v1/endpoints", headers=headers, timeout=60).json()
relay = next(e for e in endpoints if e.get("name", "").startswith("naslux-relay"))
started = time.time()
job = requests.post(f"https://api.runpod.ai/v2/{relay['id']}/runsync", headers=headers, timeout=180,
                    json={"input": {"op": "call", "method": "GET", "path": "/v1/account/billing"}}).json()
while job.get("status") in ("IN_QUEUE", "IN_PROGRESS"):
    time.sleep(5)
    job = requests.get(f"https://api.runpod.ai/v2/{relay['id']}/status/{job['id']}",
                       headers=headers, timeout=60).json()
output = job.get("output") or {}
balance = (output.get("json") or {}).get("balance")
print(f"за {time.time() - started:.0f} с: {job.get('status')}, ok={output.get('ok')}, "
      f"статус Mureka {output.get('status')}, баланс {balance} центов", flush=True)
if not output.get("ok"):
    sys.exit(f"ретранслятор не справился: {str(output)[:500]}")
