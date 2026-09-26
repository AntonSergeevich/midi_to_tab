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
    parser.add_argument("--create", action="store_true",
                        help="ещё сочинить текст (lyrics/generate) и песню с нуля (song/generate)")
    parser.add_argument("--track", action="store_true",
                        help="track/generate: новая аранжировка под вокал (целиком и по --vocals)")
    parser.add_argument("--vocals", default="", help="вокал, выделенный Demucs, mp3")
    args = parser.parse_args()
    os.makedirs(args.out, exist_ok=True)

    billing = studio.mureka_call("GET", "/v1/account/billing")
    print("Баланс Mureka:", {k: billing.get(k) for k in ("balance", "total_spending",
                                                        "concurrent_request_limit")},
          "(в центах)")

    source = studio.mureka_source(args.audio, args.out)
    print(f"Исходник для Mureka: {os.path.getsize(source) / 1e6:.1f} МБ")

    mark = billing.get("total_spending") or 0

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
        now = studio.mureka_call("GET", "/v1/account/billing").get("total_spending") or 0
        print(f"  -> remix (2 версии): списано {(now - mark) / 100:.3f} $")
        mark = now

    if not args.skip_stems:
        started = time.time()
        data = base64.b64encode(Path(source).read_bytes()).decode()
        stems = studio.mureka_call("POST", "/v1/song/stem", {
            "url": f"data:audio/mp3;base64,{data}", "model": "audio-separation-2"}, timeout=600)
        print(f"stem за {time.time() - started:.0f} с:", {k: v for k, v in stems.items()})
        for key, name in (("zip_url", "stems.zip"), ("midi_zip_url", "midi.zip")):
            if stems.get(key):
                studio.download(stems[key], os.path.join(args.out, name))
        now = studio.mureka_call("GET", "/v1/account/billing").get("total_spending") or 0
        print(f"  -> разделение: списано {(now - mark) / 100:.3f} $")
        mark = now

    def spent(label, before):
        now = studio.mureka_call("GET", "/v1/account/billing").get("total_spending") or 0
        print(f"  -> {label}: списано {(now - before) / 100:.3f} $", flush=True)
        return now

    if args.create:
        mark = studio.mureka_call("GET", "/v1/account/billing").get("total_spending") or 0
        text = studio.mureka_call("POST", "/v1/lyrics/generate", {
            "prompt": "весёлое поздравление с днём рождения для друга Саши, поп-рок"})
        print("текст:", text.get("title"), "|", (text.get("lyrics") or "")[:300].replace("\n", " / "))
        mark = spent("сочинение текста", mark)
        started = time.time()
        task = studio.mureka_call("POST", "/v1/song/generate", {
            "lyrics": text.get("lyrics") or "[Verse]\nС днём рождения", "model": "mureka-9",
            "prompt": "pop rock, upbeat, male vocal, birthday", "n": 2})
        while True:
            time.sleep(5)
            status = studio.mureka_call("GET", f"/v1/song/query/{task['id']}")
            if status.get("status") not in studio.MUREKA_STATES:
                break
        print(f"песня с нуля за {time.time() - started:.0f} с: {status.get('status')}, "
              f"модель {status.get('model')}")
        for number, choice in enumerate(status.get("choices") or [], 1):
            target = os.path.join(args.out, f"generate_{number}.mp3")
            studio.download(choice["url"], target)
        spent("песня с нуля (2 версии)", mark)

    if args.track:
        # Аранжировка под вокал оригинала: мелодия и голос не меняются, меняется всё вокруг.
        def track(path, name):
            nonlocal_mark = studio.mureka_call("GET", "/v1/account/billing").get("total_spending") or 0
            started = time.time()
            upload_id = studio.mureka_upload(path, "audio")
            task = studio.mureka_call("POST", "/v1/track/generate", {
                "generate_type": "Instrumental", "upload_audio_id": upload_id,
                "prompt": args.prompt})
            print(f"track/generate ({name}):", task)
            while True:
                time.sleep(5)
                status = studio.mureka_call("GET", f"/v1/song/query/{task['id']}")
                if status.get("status") not in studio.MUREKA_STATES:
                    break
            print(f"  за {time.time() - started:.0f} с: {status.get('status')}, модель "
                  f"{status.get('model')}, вариантов {len(status.get('choices') or [])}, "
                  f"{status.get('failed_reason') or ''}")
            files = []
            for number, choice in enumerate(status.get("choices") or [], 1):
                target = os.path.join(args.out, f"track_{name}_{number}.mp3")
                studio.download(choice["url"], target)
                files.append(target)
                print(f"  {target}: {(choice.get('duration') or 0) / 1000:.0f} с, ключи {sorted(choice)}")
            spent(f"track/generate {name}", nonlocal_mark)
            return files

        track(source, "full")
        if args.vocals:
            for number, backing in enumerate(track(args.vocals, "vocals"), 1):
                mixed = os.path.join(args.out, f"track_mix_{number}.mp3")
                os.system(f'ffmpeg -v error -y -i "{args.vocals}" -i "{backing}" -filter_complex '
                          f'"[0:a][1:a]amix=inputs=2:duration=longest:normalize=0" -b:a 192k "{mixed}"')
                print("  сведено:", mixed)

    billing = studio.mureka_call("GET", "/v1/account/billing")
    print("Баланс после:", billing.get("balance"), "центов, потрачено всего:",
          billing.get("total_spending"))


if __name__ == "__main__":
    main()
