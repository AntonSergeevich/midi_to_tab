"""AAM (Artificial Audio Multitracks, zenodo 5794629, CC BY 4.0) -> записи
с разметкой аккордов для обучения, в том же виде, что синтетика: <имя>.flac
рядом с <имя>.json [[начало, конец, "Harte-подпись"], ...].

AAM -- 3000 алгоритмически сочинённых песен из сэмплов живых инструментов,
разметка точная (звук собран из тех же MIDI). Сведения лежат в архивах по
~15 ГБ на тысячу песен; качать их целиком раннер не может, поэтому нужные
песни вынимаются из архива по HTTP Range (remotezip) -- по ~15 МБ на песню.

    python aam.py --count 600 --out aam --block 2001-3000
"""
from __future__ import annotations

import argparse
import io
import json
import re
import sys
import zipfile
from pathlib import Path

RECORD = "https://zenodo.org/records/5794629/files"
ROOT = re.compile(r"^([A-G](?:#|b)?)(.*)$")
# Суффиксы AAM -> качество Harte. Незнакомое -- «X»: не учим, не оцениваем.
QUALITY = {"": "maj", "maj": "maj", "min": "min", "m": "min", "7": "7", "dom7": "7",
           "maj7": "maj7", "min7": "min7", "m7": "min7", "dim": "dim", "aug": "aug",
           "sus2": "sus2", "sus4": "sus4", "dim7": "dim7", "hdim7": "hdim7", "min6": "min6",
           "maj6": "maj6", "6": "maj6", "9": "9", "maj9": "maj9", "min9": "min9"}


def harte(name: str) -> str:
    """'C#min' -> 'C#:min', 'Emaj' -> 'E:maj', 'N'/'' -> 'N'."""
    name = name.strip().strip("'\"")
    if name in ("", "N", "NC", "none", "None"):
        return "N"
    found = ROOT.match(name)
    if not found:
        return "X"
    quality = QUALITY.get(found.group(2))
    return f"{found.group(1)}:{quality}" if quality else "X"


def segments_from_beatinfo(text: str, end_time: float | None = None) -> list[list]:
    """beatinfo.arff (время доли, такт, доля, аккорд) -> отрезки одного аккорда."""
    rows = []
    in_data = False
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("%"):
            continue
        if line.upper().startswith("@DATA"):
            in_data = True
            continue
        if line.startswith("@"):
            continue
        if not in_data and not line[0].isdigit():
            continue
        parts = [p.strip() for p in line.split(",")]
        try:
            rows.append((float(parts[0]), harte(parts[-1])))
        except (ValueError, IndexError):
            continue
    if not rows:
        return []
    beat = rows[1][0] - rows[0][0] if len(rows) > 1 else 0.5
    out: list[list] = []
    for i, (start, label) in enumerate(rows):
        end = rows[i + 1][0] if i + 1 < len(rows) else (end_time or start + beat)
        if out and out[-1][2] == label and abs(out[-1][1] - start) < 1e-6:
            out[-1][1] = end
        else:
            out.append([start, end, label])
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--block", default="2001-3000", help="тысяча песен: 0001-1000, 1001-2000, 2001-3000")
    parser.add_argument("--count", type=int, default=600)
    parser.add_argument("--skip", type=int, default=0, help="пропустить первые N песен блока")
    parser.add_argument("--out", default="aam")
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    import urllib.request

    with urllib.request.urlopen(f"{RECORD}/{args.block}-annotations-v1.1.0.zip?download=1", timeout=300) as r:
        annotations = zipfile.ZipFile(io.BytesIO(r.read()))
    beatinfo = {Path(n).name.split("_")[0]: n for n in annotations.namelist() if n.endswith("_beatinfo.arff")}
    print(f"разметок: {len(beatinfo)}", flush=True)

    import time

    from remotezip import RemoteZip

    url = f"{RECORD}/{args.block}-audio-mixes.zip?download=1"
    vocabulary: dict[str, int] = {}
    mixes = RemoteZip(url, timeout=60)
    audio = sorted(n for n in mixes.namelist() if n.lower().endswith((".flac", ".wav", ".mp3", ".ogg")))
    print(f"сведений в архиве: {len(audio)}; например {audio[:2]}", flush=True)
    done = skipped = 0
    try:
        for name in audio[args.skip:]:
            song = Path(name).name.split("_")[0].split(".")[0]
            if song not in beatinfo:
                continue
            segments = segments_from_beatinfo(annotations.read(beatinfo[song]).decode("utf-8", "replace"))
            if not segments:
                continue
            target = out / f"aam_{song}{Path(name).suffix.lower()}"
            # Zenodo иногда рвёт соединение посреди песни: переподключаемся,
            # а после четырёх неудач пропускаем песню, а не весь прогон
            data = None
            for attempt in range(4):
                try:
                    data = mixes.read(name)
                    break
                except Exception as error:  # noqa: BLE001
                    print(f"AAM {song}: обрыв ({str(error)[:80]}), попытка {attempt + 2}", flush=True)
                    time.sleep(3 * (attempt + 1))
                    try:
                        mixes.close()
                    except Exception:  # noqa: BLE001
                        pass
                    mixes = RemoteZip(url, timeout=60)
            if data is None:
                skipped += 1
                continue
            target.write_bytes(data)
            (out / f"aam_{song}.json").write_text(json.dumps(segments))
            for _, _, label in segments:
                vocabulary[label.split(":")[-1]] = vocabulary.get(label.split(":")[-1], 0) + 1
            done += 1
            if done % 50 == 0:
                print(f"AAM: {done}/{args.count}", flush=True)
            if done >= args.count:
                break
    finally:
        mixes.close()
    print(f"AAM готово: {done} песен (пропущено из-за обрывов: {skipped}); качества аккордов: {dict(sorted(vocabulary.items(), key=lambda kv: -kv[1]))}",
          flush=True)
    if not done:
        sys.exit("AAM: ни одной песни")


if __name__ == "__main__":
    main()
