"""Проба YuE2-3B на RunPod: одна и та же песня с несколькими seed, mp3 --
в папку результатов. Только для оценки качества (веса CC BY-NC 4.0).

    RUNPOD_API_KEY=... ENDPOINT_ID=... python scripts/yue2_test.py job.json --out results
job.json: {"style": "...", "lyrics": "...", "seeds": [1, 2], "cot": "full"}
"""
import argparse
import base64
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from worker_test import report, run  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("job")
    parser.add_argument("--out", default="results")
    args = parser.parse_args()
    with open(args.job, encoding="utf-8") as f:
        job = json.load(f)
    os.makedirs(args.out, exist_ok=True)
    endpoint = os.environ["ENDPOINT_ID"]
    headers = {"Authorization": f"Bearer {os.environ['RUNPOD_API_KEY']}"}
    ping = run(endpoint, headers, {"mode": "ping"}, limit=1800)
    print("ping:", json.dumps(ping.get("output"), ensure_ascii=False))
    report(ping)
    if not (ping.get("output") or {}).get("cuda"):
        sys.exit("Воркер не видит видеокарту -- проба остановлена")
    failed = 0
    for seed in job.get("seeds") or [831001]:
        status = run(endpoint, headers, {"mode": job.get("mode", "create"), "style": job["style"],
                                         "lyrics": job.get("lyrics", ""), "seed": seed, "variants": 1,
                                         "cfg_scale": job.get("cfg_scale"),
                                         **({"audio_url": job["audio_url"]} if job.get("audio_url") else {})})
        output = status.get("output") or {}
        print(f"seed {seed}:", json.dumps({k: v for k, v in output.items() if k != "files"},
                                         ensure_ascii=False)[:1500])
        with open(os.path.join(args.out, f"{seed}_info.json"), "w", encoding="utf-8") as f:
            json.dump({k: v for k, v in output.items() if k != "files"} or status, f,
                      ensure_ascii=False, indent=1)
        if not output.get("ok"):
            # Итог -- в аннотацию: лог прогона из облачной сессии не скачать
            print(f"::error::seed {seed}: {(output.get('error') or status.get('error') or '')[:900]}")
        report(status)
        if not output.get("ok"):
            failed += 1
            continue
        for item in output["files"]:
            with open(os.path.join(args.out, f"{seed}_{item['name']}"), "wb") as f:
                f.write(base64.b64decode(item["audio_b64"]))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
