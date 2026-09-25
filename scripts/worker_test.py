"""Живой тест GPU-воркера NASLUX на RunPod: отправить задачу, дождаться,
сохранить файлы и напечатать время и примерную цену.

Сначала ping: воркер сообщает, видит ли он видеокарту и какие модели
загрузил. Если CUDA нет -- дальше не идём, считать на процессоре дорого.

    python scripts/worker_test.py song.mp3 --mode restyle --strength 0.3 --out results/
"""
import argparse
import base64
import json
import os
import subprocess
import sys
import time

import requests

# Serverless flex, $/сек: 24 ГБ (A5000/A4500/L4) и 24 ГБ PRO (4090).
PRICE_PER_SECOND = {"24GB": 0.00019, "24GB PRO": 0.00031}
USD_RUB = 95


def run(endpoint, headers, job_input, limit=2400):
    started = time.time()
    job = requests.post(f"https://api.runpod.ai/v2/{endpoint}/run", headers=headers,
                        timeout=120, json={"input": job_input})
    print("run ->", job.status_code, job.text[:300])
    job.raise_for_status()
    job_id = job.json()["id"]
    while True:
        time.sleep(10)
        status = requests.get(f"https://api.runpod.ai/v2/{endpoint}/status/{job_id}",
                              headers=headers, timeout=60).json()
        state = status.get("status")
        print(f"{time.time() - started:6.0f} с  {state}", flush=True)
        if state in ("COMPLETED", "FAILED", "CANCELLED", "TIMED_OUT"):
            return status
        if time.time() - started > limit:
            requests.post(f"https://api.runpod.ai/v2/{endpoint}/cancel/{job_id}",
                          headers=headers, timeout=60)
            sys.exit(f"Не дождались за {limit} с, задача отменена")


def report(status):
    queued = status.get("delayTime", 0) / 1000
    worked = status.get("executionTime", 0) / 1000
    print(f"Ожидание воркера: {queued:.0f} с, работа: {worked:.0f} с")
    for gpu, price in PRICE_PER_SECOND.items():
        cost = (queued + worked) * price
        print(f"  {gpu}: ~${cost:.3f} (~{cost * USD_RUB:.1f} ₽), без холодного старта "
              f"~{worked * price * USD_RUB:.1f} ₽")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("audio")
    parser.add_argument("--mode", default="restyle")
    parser.add_argument("--seconds", type=int, default=60)
    parser.add_argument("--strength", type=float, default=0.3)
    parser.add_argument("--knobs", default="",
                        help="влияние песни,влияние стиля,странность[,turbo] -- например 0.35,0.6,0.3")
    parser.add_argument("--track", default="drums")
    parser.add_argument("--prompt", default="nu metal, heavy downtuned 7-string guitars, "
                        "aggressive drums, distorted bass, powerful male vocals")
    parser.add_argument("--lyrics", default="")
    parser.add_argument("--steps", type=int, default=0)
    parser.add_argument("--extra", default="",
                        help='JSON поверх задания, напр. {"variants": 2, "raw": {"shift": 3}}')
    parser.add_argument("--out", default="results")
    args = parser.parse_args()

    endpoint = os.environ["ENDPOINT_ID"]
    headers = {"Authorization": f"Bearer {os.environ['RUNPOD_API_KEY']}"}
    os.makedirs(args.out, exist_ok=True)

    ping = run(endpoint, headers, {"mode": "ping"}, limit=1500)
    print("ping:", json.dumps(ping.get("output"), ensure_ascii=False)[:1500])
    report(ping)
    if not (ping.get("output") or {}).get("cuda"):
        sys.exit("Воркер не видит видеокарту -- тест остановлен")

    audio = subprocess.run(["ffmpeg", "-v", "error", "-i", args.audio, "-t", str(args.seconds),
                            "-ac", "2", "-b:a", "192k", "-f", "mp3", "-"],
                           check=True, capture_output=True).stdout
    job_input = {"mode": args.mode, "audio_b64": base64.b64encode(audio).decode(),
                 "prompt": args.prompt, "lyrics": args.lyrics, "strength": args.strength,
                 "track": args.track, "bitrate": 128}
    if args.knobs:
        parts = args.knobs.split(",")
        job_input.update(audio_influence=float(parts[0]), style_influence=float(parts[1]),
                         weirdness=float(parts[2]))
        if len(parts) > 3 and parts[3].strip() == "turbo":
            job_input["engine"] = "turbo"
    if args.extra:
        job_input.update(json.loads(args.extra))
    if args.steps:
        job_input["steps"] = args.steps
    status = run(endpoint, headers, job_input)
    output = status.get("output") or {}
    print("Итог:", json.dumps({k: v for k, v in output.items() if k != "files"},
                              ensure_ascii=False)[:1500])
    report(status)
    if not output.get("ok"):
        sys.exit(f"Задача не выполнена: {output.get('error') or status.get('error')}")
    for item in output["files"]:
        path = os.path.join(args.out, item["name"])
        with open(path, "wb") as f:
            f.write(base64.b64decode(item["audio_b64"]))
        print(f"  {path}: {os.path.getsize(path) / 1e6:.1f} МБ")


if __name__ == "__main__":
    main()
