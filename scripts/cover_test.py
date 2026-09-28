"""Кавер по документации Mureka (song/remix) с настоящим текстом песни:
текст распознаётся song/recognize, а не вписывается руками. Сходство мелодии
с исходником -- той же меркой, что scripts/melody_test.py.

    MUREKA_API_KEY=... python scripts/cover_test.py source.mp3 --out cover
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from melody_test import contour, similarity, vocals  # noqa: E402
from web import studio  # noqa: E402

STYLE = "nu metal, heavy distorted guitars, powerful drums, aggressive"


def lyrics_from(recognized: dict) -> str:
    """Секции recognize -> текст с пометками [Verse]/[Chorus] по порядку."""
    parts = []
    for number, section in enumerate(recognized.get("lyrics_sections") or [], 1):
        lines = [l.get("text", "").strip() for l in section.get("lines") or [] if l.get("text", "").strip()]
        if lines:
            parts.append(f"[Section {number}]\n" + "\n".join(lines))
    return "\n\n".join(parts)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("audio")
    parser.add_argument("--out", default="cover")
    args = parser.parse_args()
    os.makedirs(args.out, exist_ok=True)
    report = {}
    mark = studio.mureka_call("GET", "/v1/account/billing").get("total_spending") or 0
    reference = contour(vocals(args.audio, "source"))

    audio_id = studio.mureka_upload(args.audio, "audio")
    recognized = studio.mureka_call("POST", "/v1/song/recognize", {"upload_audio_id": audio_id}, timeout=600)
    text = lyrics_from(recognized)
    now = studio.mureka_call("GET", "/v1/account/billing").get("total_spending") or 0
    print(f"распознано строк: {len(text.splitlines())} (${(now - mark) / 100:.3f})", flush=True)
    report["recognize_cost"] = (now - mark) / 100
    report["lyrics_lines"] = len(text.splitlines())  # сам текст не сохраняем: репозиторий публичный
    mark = now

    remix_id = studio.mureka_upload(args.audio, "remix")
    task = studio.mureka_call("POST", "/v1/song/remix", {
        "upload_audio_id": remix_id, "prompt": STYLE, "lyrics": text or "[Verse]\nLa la la", "n": 2})
    while True:
        time.sleep(5)
        status = studio.mureka_call("GET", f"/v1/song/query/{task['id']}")
        if status.get("status") not in studio.MUREKA_STATES:
            break
    scores = []
    for number, choice in enumerate(status.get("choices") or [], 1):
        target = os.path.join(args.out, f"cover_{number}.mp3")
        studio.download(choice["url"], target)
        scores.append(similarity(reference, contour(vocals(target, f"cover_{number}"))))
    now = studio.mureka_call("GET", "/v1/account/billing").get("total_spending") or 0
    report["remix_with_real_lyrics"] = {"similarity": scores, "cost_usd": (now - mark) / 100}
    print("remix с настоящим текстом: сходство", scores, f"(${(now - mark) / 100:.2f})", flush=True)
    Path(args.out, "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
