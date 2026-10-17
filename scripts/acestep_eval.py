"""Замер генерации песен на русском: ACE-Step 1.5 в нескольких настройках и
YuE2 -- одни и те же тексты (свои, scripts/acestep_job_ru.json), одна длина,
один seed. mp3 и сведения (время, модель) -- в папку результатов; оценку
считает scripts/acestep_score.py.

    RUNPOD_API_KEY=... ENDPOINT_ID=... python scripts/acestep_eval.py job.json --engine ace --out results
    RUNPOD_API_KEY=... ENDPOINT_ID=... python scripts/acestep_eval.py job.json --engine yue2 --out results
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
    parser.add_argument("--engine", choices=["ace", "yue2"], default="ace")
    parser.add_argument("--only", default="", help="варианты через запятую (по умолчанию все)")
    parser.add_argument("--out", default="results")
    args = parser.parse_args()
    with open(args.job, encoding="utf-8") as f:
        job = json.load(f)
    os.makedirs(args.out, exist_ok=True)
    endpoint = os.environ["ENDPOINT_ID"]
    headers = {"Authorization": f"Bearer {os.environ['RUNPOD_API_KEY']}"}
    ping = run(endpoint, headers, {"mode": "ping"}, limit=2400)
    print("ping:", json.dumps(ping.get("output"), ensure_ascii=False)[:1500])
    report(ping)
    if not (ping.get("output") or {}).get("cuda"):
        sys.exit("Воркер не видит видеокарту -- замер остановлен")
    wanted = [v for v in args.only.split(",") if v] or list(job["variants"])
    variants = {"yue2": {"title": "YuE2 (сейчас на сайте)", "raw": {}}} if args.engine == "yue2" \
        else {k: job["variants"][k] for k in wanted}
    failed = 0
    # Сначала все песни одной модели, потом другой: сервер не перегружает веса туда-сюда
    for name, variant in variants.items():
        for song in job["songs"]:
            if args.engine == "yue2":
                task = {"mode": "create", "style": f"{song['style']}, {song['bpm']} BPM", "lyrics": song["lyrics"],
                        "seed": job["seed"], "variants": 1}
            else:
                task = {"mode": "create", "prompt": song["style"], "lyrics": song["lyrics"], "language": "ru",
                        "song_seconds": job["seconds"], "seed": job["seed"], "variants": 1,
                        "raw": {"bpm": song["bpm"], **variant["raw"]}}
            status = run(endpoint, headers, task, limit=2400)
            output = status.get("output") or {}
            meta = {"song": song["id"], "variant": name, "title": variant["title"], "ok": bool(output.get("ok")),
                    "seconds_worked": round(status.get("executionTime", 0) / 1000, 1),
                    "seconds_queued": round(status.get("delayTime", 0) / 1000, 1),
                    "info": {k: v for k, v in (output.get("info") or {}).items() if k not in ("lyrics", "abc", "plan")},
                    "error": str(output.get("error") or status.get("error") or "")[-1500:]}
            print(f"{name}/{song['id']}:", json.dumps({k: v for k, v in meta.items() if k != "info"}, ensure_ascii=False))
            report(status)
            with open(os.path.join(args.out, f"{name}__{song['id']}.json"), "w", encoding="utf-8") as f:
                json.dump(meta, f, ensure_ascii=False, indent=1)
            if not meta["ok"] or not output.get("files"):
                print(f"::error::{name}/{song['id']}: " + meta["error"].replace("\n", " | "))
                failed += 1
                continue
            with open(os.path.join(args.out, f"{name}__{song['id']}.mp3"), "wb") as f:
                f.write(base64.b64decode(output["files"][0]["audio_b64"]))
    sys.exit(1 if failed == sum(1 for _ in variants) * len(job["songs"]) else 0)


if __name__ == "__main__":
    main()
