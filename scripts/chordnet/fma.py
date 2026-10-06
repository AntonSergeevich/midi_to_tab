"""Живые песни с голосом из Free Music Archive -- только под свободными
лицензиями, где можно и в коммерческой работе (CC BY, CC BY-SA, CC0).

Модель аккордов училась на синтетике, соло-гитаре GuitarSet и песнях AAM,
собранных из сэмплов, -- без голоса и живого сведения. На этом она почти
безошибочна (AAM 0.96), а на русских песнях владельца -- около 88%: мешает
как раз то, чего она не слышала. Здесь -- 30-секундные отрывки FMA
(fma_large, Defferrard et al. 2017); разметку аккордов к ним ставит
pseudo.py. Звук не хранится и не публикуется: после признаков он удаляется,
список авторов и лицензий -- в attribution.csv.

    python fma.py --count 750 --skip 0 --out fma
"""
from __future__ import annotations

import argparse
import csv
import os
import random
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

BASE = "https://os.unil.cloud.switch.ch/fma"
# Доли жанров: русский репертуар владельца -- поп, рок, рэп; остальное --
# разнообразие звучания. Классика, джаз, спокен -- мимо словаря аккордов.
GENRES = {"Rock": 0.30, "Pop": 0.20, "Folk": 0.12, "Hip-Hop": 0.08, "Electronic": 0.08,
          "International": 0.07, "Soul-RnB": 0.05, "Country": 0.04, "Blues": 0.03,
          "Easy Listening": 0.03}


def free(license_text: str) -> bool:
    """CC BY / CC BY-SA / CC0 / общественное достояние -- да; NC и ND -- нет."""
    s = (license_text or "").lower().replace("-", "").replace(" ", "")
    if "noncommercial" in s or "noderiv" in s:
        return False
    return "attribution" in s or "cc0" in s or "publicdomain" in s or "zero" in s


def catalog(work: Path):
    """Треки fma_large под свободной лицензией: [(id, жанр, автор, название, лицензия, адрес)]."""
    import pandas as pd
    from remotezip import RemoteZip

    table = work / "tracks.csv"
    if not table.exists():
        with RemoteZip(f"{BASE}/fma_metadata.zip") as archive:
            with archive.open("fma_metadata/tracks.csv") as src, open(table, "wb") as dst:
                dst.write(src.read())
    tracks = pd.read_csv(table, index_col=0, header=[0, 1], low_memory=False)
    out = []
    for tid, row in tracks.iterrows():
        genre = row[("track", "genre_top")]
        if genre not in GENRES or not free(str(row[("track", "license")])):
            continue
        out.append((int(tid), genre, str(row[("artist", "name")]), str(row[("track", "title")]),
                    str(row[("track", "license")]), f"https://freemusicarchive.org/track/{int(tid)}"))
    return out


def pick(found, count: int, skip: int, seed: int = 0):
    """Одна и та же выборка при каждом запуске; skip -- для кусков по 750."""
    rng = random.Random(seed)
    by_genre = {}
    for item in found:
        by_genre.setdefault(item[1], []).append(item)
    for items in by_genre.values():
        rng.shuffle(items)
    total = count + skip
    wanted = []
    for genre, share in GENRES.items():
        wanted += by_genre.get(genre, [])[:round(total * share)]
    rng.shuffle(wanted)
    return wanted[skip:skip + count]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=750)
    parser.add_argument("--skip", type=int, default=0)
    parser.add_argument("--out", default="fma")
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    found = catalog(out)
    print(f"свободных треков нужных жанров: {len(found)}", flush=True)
    chosen = pick(found, args.count, args.skip)
    print(f"берём {len(chosen)} (с {args.skip})", flush=True)

    import threading

    from remotezip import RemoteZip

    # Оглавление архива (106 тысяч файлов) читается один раз на поток
    local = threading.local()

    def fetch(item):
        tid = item[0]
        target = out / f"fma_{tid:06d}.mp3"
        if target.exists():
            return True
        for attempt in range(4):
            try:
                if getattr(local, "archive", None) is None:
                    local.archive = RemoteZip(f"{BASE}/fma_large.zip", timeout=60)
                target.write_bytes(local.archive.read(f"fma_large/{tid // 1000:03d}/{tid:06d}.mp3"))
                return True
            except KeyError:
                return False                      # в архиве отрывка нет
            except Exception as error:  # noqa: BLE001 -- сервер иногда рвёт соединение
                local.archive = None
                if attempt == 3:
                    print(f"{tid}: не скачался ({error!r})", file=sys.stderr, flush=True)
                time.sleep(2 ** attempt)
        return False

    done = 0
    with ThreadPoolExecutor(8) as pool:
        for number, ok in enumerate(pool.map(fetch, chosen), 1):
            done += ok
            if number % 100 == 0:
                print(f"скачано {done}/{number}", flush=True)
    with open(out / "attribution.csv", "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["track_id", "genre", "artist", "title", "license", "url"])
        writer.writerows(chosen)
    print(f"готово: {done} из {len(chosen)}", flush=True)
    os.remove(out / "tracks.csv")


if __name__ == "__main__":
    main()
