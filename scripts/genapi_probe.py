"""Проба Suno на GenAPI: задание (стиль, текст) -> треки и сырые ответы в папку.

    GENAPI_API_KEY=... python scripts/genapi_probe.py scripts/suno_job_naslux_drive.json --out results

Файл задания: {"task": "generate", "title", "style", "lyrics", "instrumental",
"gender", "model", "upload_url"}. Сырые ответы сохраняются (ключа в них нет),
чтобы по ним подогнать разбор ответа на сайте.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from web import genapi  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("job")
    parser.add_argument("--out", default="results")
    parser.add_argument("--request", default="", help="забрать готовый запрос, не заказывая новый")
    args = parser.parse_args()
    job = json.load(open(args.job, encoding="utf-8"))
    os.makedirs(args.out, exist_ok=True)
    # «params» -- запрос как есть (дорожки, WAV, таймкоды, продление): для проб цен
    params = {"translate_input": False, "model": "v6", **job["params"]} if job.get("params") else \
        genapi.suno_params(job.get("task", "generate"), title=job.get("title", ""),
        style=job.get("style", ""), lyrics=job.get("lyrics", ""),
        instrumental=job.get("instrumental", False), gender=job.get("gender", ""),
        model=job.get("model", ""), upload_url=job.get("upload_url", ""))
    t = time.time()
    if args.request:
        rid = args.request
    else:
        started = genapi.call("POST", f"/networks/{genapi.SUNO}", params)
        json.dump(started, open(f"{args.out}/start.json", "w"), ensure_ascii=False, indent=1)
        rid = genapi.request_id(started)
        print(f"запрос {rid}: {json.dumps(started, ensure_ascii=False)[:300]}", flush=True)
    try:
        state = genapi.wait(rid, timeout=900, every=10,
                            progress=lambda stage, s: print(f"  {stage}, {s:.0f} с", flush=True))
    finally:
        json.dump(genapi.status(rid), open(f"{args.out}/result.json", "w"), ensure_ascii=False, indent=1)
    found = genapi.tracks(state)
    print(f"::notice title=Suno {params['model']} за {time.time() - t:.0f} с, цена {state.get('cost')}::"
          f"треков {len(found)}: " + "; ".join(f"{x['title']} {x['seconds']} с" for x in found))
    print(f"::notice title=Цена {params.get('task')} {params.get('separation_type', '')}::{state.get('cost')} ₽, "
          f"{state.get('runtime')} с, файлов {len(found)}")
    for number, track in enumerate(found, 1):
        size = genapi.download(track["url"], f"{args.out}/suno_{number}.mp3")
        print(f"  suno_{number}.mp3 {size // 1024} КБ", flush=True)


if __name__ == "__main__":
    main()
