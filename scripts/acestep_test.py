"""Живой тест ACE-Step на RunPod: дописать партии поверх исходного трека.

Режет первые N секунд, кодирует в mp3 192k (у /run лимит тела 10 МБ),
отправляет на эндпоинт, ждёт результат и печатает время и примерную цену.
Запускается из GitHub Actions: у облачной сессии доступ к api.runpod.ai закрыт.

    python scripts/acestep_test.py song.mp3 --seconds 60 --out cover.mp3
"""
import argparse
import base64
import os
import subprocess
import sys
import time

import requests

# Serverless flex, $/сек: 24 ГБ (A5000/A4500/L4) и 24 ГБ PRO (4090).
# Точная цена -- в биллинге RunPod; здесь оценка по прайсу.
PRICE_PER_SECOND = {"24GB": 0.00019, "24GB PRO": 0.00031}
USD_RUB = 95


def clip(path: str, seconds: int) -> bytes:
    return subprocess.run(
        ["ffmpeg", "-v", "error", "-i", path, "-t", str(seconds), "-ac", "2",
         "-b:a", "192k", "-f", "mp3", "-"],
        check=True, capture_output=True).stdout


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("audio")
    parser.add_argument("--seconds", type=int, default=60)
    parser.add_argument("--prompt", default="nu metal, heavy downtuned guitars, "
                        "aggressive drums, distorted bass, energetic")
    parser.add_argument("--out", default="cover.mp3")
    args = parser.parse_args()

    endpoint = os.environ["ENDPOINT_ID"]
    headers = {"Authorization": f"Bearer {os.environ['RUNPOD_API_KEY']}"}
    audio = clip(args.audio, args.seconds)
    print(f"Исходник: {args.seconds} с, {len(audio) / 1e6:.1f} МБ")

    started = time.time()
    job = requests.post(f"https://api.runpod.ai/v2/{endpoint}/run", headers=headers, timeout=120,
                        json={"input": {"audio_b64": base64.b64encode(audio).decode(),
                                        "prompt": args.prompt, "lyrics": "",
                                        "duration": args.seconds, "thinking": False}})
    print("run ->", job.status_code, job.text[:500])
    job.raise_for_status()
    job_id = job.json()["id"]

    while True:
        time.sleep(10)
        status = requests.get(f"https://api.runpod.ai/v2/{endpoint}/status/{job_id}",
                              headers=headers, timeout=60).json()
        state = status.get("status")
        print(f"{time.time() - started:6.0f} с  {state}")
        if state in ("COMPLETED", "FAILED", "CANCELLED", "TIMED_OUT"):
            break
        if time.time() - started > 2400:
            requests.post(f"https://api.runpod.ai/v2/{endpoint}/cancel/{job_id}",
                          headers=headers, timeout=60)
            sys.exit("Не дождались за 40 минут, задача отменена")

    output = status.get("output")
    shown = {k: v for k, v in status.items() if k != "output"}
    print("Итог:", shown)
    if state != "COMPLETED" or not isinstance(output, dict) or not output.get("audio_b64"):
        print("output:", str(output)[:2000])
        sys.exit(f"Генерация не удалась: {state}")

    with open(args.out, "wb") as f:
        f.write(base64.b64decode(output["audio_b64"].split(",")[-1]))
    queued = status.get("delayTime", 0) / 1000
    worked = status.get("executionTime", 0) / 1000
    print(f"Готово: {args.out}, {os.path.getsize(args.out) / 1e6:.1f} МБ")
    print(f"Ожидание воркера (холодный старт): {queued:.0f} с, генерация: {worked:.0f} с, "
          f"по мнению воркера: {output.get('secondes')} с")
    for gpu, price in PRICE_PER_SECOND.items():
        cost = (queued + worked) * price
        print(f"Цена на {gpu}: ~${cost:.3f} (~{cost * USD_RUB:.1f} ₽) с холодным стартом, "
              f"~${worked * price:.3f} (~{worked * price * USD_RUB:.1f} ₽) на тёплом воркере")


if __name__ == "__main__":
    main()
