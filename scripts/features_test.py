"""Живой тест новых функций Mureka перед тем, как ставить их на сайт:
клон голоса (song/vocal-clone -> vocal_id -> song/generate), песня «как в
образце» (files/upload purpose=reference -> reference_id), продление песни
(lyrics/extend + song/extend). Печатает, что пришло и сколько списано.

    MUREKA_API_KEY=... python scripts/features_test.py source.mp3 --out features
"""
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from web import studio  # noqa: E402

LYRICS = "[Verse]\nЯ включаю свет в пустом окне\nГород спит, а песня бьётся во мне\n[Chorus]\nМы звучим до утра"


def spent(mark: int, label: str) -> int:
    now = studio.mureka_call("GET", "/v1/account/billing").get("total_spending") or 0
    print(f"   -> {label}: списано ${(now - mark) / 100:.3f}", flush=True)
    return now


def song(body: dict, out: str, name: str) -> dict:
    task = studio.mureka_call("POST", "/v1/song/generate", body)
    return finish(task, out, name)


def finish(task: dict, out: str, name: str, kind: str = "song") -> dict:
    while True:
        time.sleep(5)
        status = studio.mureka_call("GET", f"/v1/{kind}/query/{task['id']}")
        if status.get("status") not in studio.MUREKA_STATES:
            break
    print(f"{name}: {status.get('status')} {status.get('failed_reason') or ''}", flush=True)
    for number, choice in enumerate(status.get("choices") or [], 1):
        studio.download(choice["url"], os.path.join(out, f"{name}_{number}.mp3"))
        print(f"   вариант {number}: id={choice.get('id')}, {choice.get('duration', 0) / 1000:.0f} с, "
              f"ключи {sorted(choice)}", flush=True)
    return status


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("audio")
    parser.add_argument("--out", default="features")
    args = parser.parse_args()
    os.makedirs(args.out, exist_ok=True)
    report = {}
    mark = studio.mureka_call("GET", "/v1/account/billing").get("total_spending") or 0

    # 1. Клон голоса: 25 секунд чистого вокала
    subprocess.run([sys.executable, "-m", "demucs", "--two-stems=vocals", "-n", "htdemucs", "-o", "sep",
                    args.audio], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    voice = os.path.join(args.out, "voice.mp3")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", "5", "-t", "25", "-i",
                    f"sep/htdemucs/{Path(args.audio).stem}/vocals.wav", "-ac", "1", "-b:a", "192k", voice],
                   check=True)
    try:
        vocal_id = studio.mureka_upload(voice, "", "/v1/song/vocal-clone",
                                        {"description": "NASLUX test voice"}, key="vocal_id")
        print("vocal_id:", vocal_id, flush=True)
        mark = spent(mark, "клон голоса")
        status = song({"lyrics": LYRICS, "vocal_id": vocal_id, "prompt": "pop rock, energetic",
                       "model": studio.MUREKA_SONG_MODEL, "n": 2}, args.out, "clone_song")
        mark = spent(mark, "песня клоном голоса")
        report["clone"] = {"vocal_id": vocal_id, "status": status.get("status")}
    except Exception as error:  # noqa: BLE001
        print("клон голоса:", error, flush=True)
        report["clone"] = str(error)

    # 2. Песня «как в образце» (30 с)
    reference = os.path.join(args.out, "reference.mp3")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", "10", "-t", "30", "-i", args.audio,
                    "-b:a", "192k", reference], check=True)
    try:
        reference_id = studio.mureka_upload(reference, "reference")
        status = song({"lyrics": LYRICS, "reference_id": reference_id,
                       "model": studio.MUREKA_SONG_MODEL, "n": 2}, args.out, "reference_song")
        mark = spent(mark, "песня по образцу")
        report["reference"] = status.get("status")
        first = (status.get("choices") or [{}])[0]
    except Exception as error:  # noqa: BLE001
        print("образец:", error, flush=True)
        report["reference"] = str(error)
        first = {}

    # 3. Продление песни: текст продолжения нейросетью + song/extend
    if first.get("id"):
        more = studio.mureka_call("POST", "/v1/lyrics/extend", {"lyrics": LYRICS})
        print("продолжение текста:", (more.get("lyrics") or "")[:160].replace("\n", " / "), flush=True)
        mark = spent(mark, "продолжение текста")
        try:
            task = studio.mureka_call("POST", "/v1/song/extend", {
                "song_id": first["id"], "lyrics": more.get("lyrics") or "[Verse]\nИ снова свет",
                "extend_at": first.get("duration") or 60000})
            status = finish(task, args.out, "extended")
            mark = spent(mark, "продление песни")
            report["extend"] = status.get("status")
        except Exception as error:  # noqa: BLE001
            print("продление:", error, flush=True)
            report["extend"] = str(error)
    Path(args.out, "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
