"""Живая проверка Mureka тем же кодом, что на сайте (web/studio.py).

    MUREKA_API_KEY=... python scripts/mureka_test.py song.mp3 --lyrics-file text.txt --out results/

Печатает баланс, делает remix (две версии) и разделение на партии
(audio-separation-2: до 12 партий и MIDI), складывает всё в --out.
"""
import argparse
import base64
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from web import studio  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("audio")
    parser.add_argument("--prompt", default=studio.PRESETS["numetal"][1])
    parser.add_argument("--lyrics-file", default="")
    parser.add_argument("--out", default="results")
    parser.add_argument("--skip-remix", action="store_true")
    parser.add_argument("--skip-stems", action="store_true")
    args = parser.parse_args()
    os.makedirs(args.out, exist_ok=True)

    billing = studio.mureka_call("GET", "/v1/account/billing")
    print("Баланс Mureka:", {k: billing.get(k) for k in ("balance", "total_spending",
                                                        "concurrent_request_limit")},
          "(в центах)")

    source = studio.mureka_source(args.audio, args.out)
    print(f"Исходник для Mureka: {os.path.getsize(source) / 1e6:.1f} МБ")

    if not args.skip_remix:
        lyrics = Path(args.lyrics_file).read_text() if args.lyrics_file else \
            "[Verse]\nWe are angels in the night\n[Chorus]\nWe are angels, we are light"
        started = time.time()
        upload_id = studio.mureka_upload(source, "remix")
        task = studio.mureka_call("POST", "/v1/song/remix", {
            "upload_audio_id": upload_id, "prompt": args.prompt, "lyrics": lyrics, "n": 2})
        print("remix:", task)
        while True:
            time.sleep(5)
            status = studio.mureka_call("GET", f"/v1/song/query/{task['id']}")
            print(f"{time.time() - started:5.0f} с  {status.get('status')}", flush=True)
            if status.get("status") not in studio.MUREKA_STATES:
                break
        if status.get("status") != "succeeded":
            sys.exit(f"remix не удался: {status}")
        print("модель:", status.get("model"))
        for number, choice in enumerate(status.get("choices") or [], 1):
            target = os.path.join(args.out, f"remix_{number}.mp3")
            studio.download(choice["url"], target)
            print(f"  {target}: {os.path.getsize(target) / 1e6:.1f} МБ, "
                  f"{(choice.get('duration') or 0) / 1000:.0f} с")

    if not args.skip_stems:
        started = time.time()
        data = base64.b64encode(Path(source).read_bytes()).decode()
        stems = studio.mureka_call("POST", "/v1/song/stem", {
            "url": f"data:audio/mp3;base64,{data}", "model": "audio-separation-2"}, timeout=600)
        print(f"stem за {time.time() - started:.0f} с:", {k: v for k, v in stems.items()})
        for key, name in (("zip_url", "stems.zip"), ("midi_zip_url", "midi.zip")):
            if stems.get(key):
                studio.download(stems[key], os.path.join(args.out, name))

    billing = studio.mureka_call("GET", "/v1/account/billing")
    print("Баланс после:", billing.get("balance"), "центов, потрачено всего:",
          billing.get("total_spending"))


if __name__ == "__main__":
    main()
