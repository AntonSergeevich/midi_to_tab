"""Замер разделения: каждая песня -- партии тремя способами (sep_test/handler.py).
Песни -- из прошлого замера генерации (test-results/acestep-39040138366, тексты
наши). Партии -- mp3 в папку результатов: <песня>__<способ>__<партия>.mp3.

    RUNPOD_API_KEY=... ENDPOINT_ID=... python scripts/sep_eval.py songs/*.mp3 --out results
"""
import argparse
import base64
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from worker_test import report, run  # noqa: E402

METHODS = ["demucs6s", "sw", "chain"]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("songs", nargs="+")
    parser.add_argument("--out", default="results")
    args = parser.parse_args()
    os.makedirs(args.out, exist_ok=True)
    endpoint = os.environ["ENDPOINT_ID"]
    headers = {"Authorization": f"Bearer {os.environ['RUNPOD_API_KEY']}"}
    ping = run(endpoint, headers, {"mode": "ping"}, limit=2400)
    print("ping:", json.dumps(ping.get("output"), ensure_ascii=False))
    report(ping)
    summary, failed = [], 0
    for song in args.songs:
        name = os.path.basename(song).rsplit(".", 1)[0]
        with open(song, "rb") as f:
            audio = base64.b64encode(f.read()).decode()
        for method in METHODS:
            status = run(endpoint, headers, {"method": method, "audio_b64": audio}, limit=2400)
            output = status.get("output") or {}
            report(status)
            row = {"song": name, "method": method, "ok": bool(output.get("ok")),
                   "separate_seconds": output.get("separate_seconds"),
                   "gpu_seconds": round(status.get("executionTime", 0) / 1000, 1),
                   "parts": [x["name"] for x in output.get("files") or []], "error": output.get("error")}
            print(json.dumps(row, ensure_ascii=False))
            summary.append(row)
            if not row["ok"]:
                failed += 1
                print(f"::error::{name}/{method}: {row['error']}")
                continue
            for item in output["files"]:
                with open(os.path.join(args.out, f"{name}__{method}__{item['name']}"), "wb") as f:
                    f.write(base64.b64decode(item["audio_b64"]))
    with open(os.path.join(args.out, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)
    sys.exit(1 if failed == len(summary) else 0)


if __name__ == "__main__":
    main()
