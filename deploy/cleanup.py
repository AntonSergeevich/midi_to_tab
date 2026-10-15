#!/usr/bin/env python3
"""
Уборка старых файлов.

Партии одной песни в WAV весят около 250 МБ: сорокагигабайтный диск они
заполняют примерно за сто шестьдесят треков. Поэтому исходники и партии
удаляются по возрасту, а результаты разбора -- аккорды, табы, MIDI --
остаются: они занимают килобайты и ради них человек и приходил.

Запускается таймером systemd раз в сутки. Сроки задаются переменными
окружения, чтобы менять их без правки кода.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
import time
from pathlib import Path

DATA_DIR = Path(os.environ.get("MIDI2TAB_DATA", "/opt/nasluh/data"))
KEEP_UPLOADS_DAYS = float(os.environ.get("NASLUH_KEEP_UPLOADS_DAYS", "14"))
KEEP_STEMS_DAYS = float(os.environ.get("NASLUH_KEEP_STEMS_DAYS", "14"))
# Треки Студии (mp3, единицы мегабайт) -- 60 дней с ПОСЛЕДНЕГО открытия:
# сайт обновляет время папки, когда трек слушают или открывают в
# мультитреке. 14 дней с создания (до 15.10) удаляли песню, с которой
# владелец репетировал. Избранное (★) не удаляется никогда.
KEEP_STUDIO_DAYS = float(os.environ.get("NASLUH_KEEP_STUDIO_DAYS", "60"))
# Ниже этой доли свободного места удаляем агрессивнее, не дожидаясь срока
LOW_DISK_RATIO = float(os.environ.get("NASLUH_LOW_DISK_RATIO", "0.12"))


def human(size: float) -> str:
    for unit in ("Б", "КБ", "МБ", "ГБ"):
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} ТБ"


def folder_size(path: Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += (Path(root) / name).stat().st_size
            except OSError:
                pass
    return total


def free_ratio(path: Path) -> float:
    usage = shutil.disk_usage(path)
    return usage.free / usage.total


def favorites(data_dir: Path) -> set[str]:
    """Треки в избранном (★) -- их файлы не трогаем. Нет базы -- пусто."""
    db = data_dir / "app.db"
    if not db.is_file():
        return set()
    try:
        with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as conn:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(jobs)")}
            if "favorite" not in columns:
                return set()
            keep = {row[0] for row in conn.execute("SELECT id FROM jobs WHERE favorite = 1")}
            # И всё, что сделано из избранного трека: его дорожки, версии,
            # разборы -- иначе мультитрек избранной песни остался бы пустым
            for _ in range(3):
                more = {row[0] for row in conn.execute(
                    "SELECT id FROM jobs WHERE json_extract(settings, '$.from') IN (SELECT value FROM json_each(?))"
                    " OR json_extract(settings, '$.parent') IN (SELECT value FROM json_each(?))"
                    " OR json_extract(settings, '$.studio') IN (SELECT value FROM json_each(?))",
                    (json.dumps(sorted(keep)),) * 3)}
                if more <= keep:
                    break
                keep |= more
            return keep
    except sqlite3.Error as exc:
        print(f"  база недоступна, избранное не прочитано: {exc}", file=sys.stderr)
        raise


def sweep(root: Path, max_age_days: float, label: str, keep: set[str] = frozenset()) -> tuple[int, int]:
    """Удалить папки старше срока, кроме keep. Возвращает (сколько, сколько байт)."""
    if not root.is_dir():
        return 0, 0
    deadline = time.time() - max_age_days * 86400
    removed = freed = 0
    for entry in sorted(root.iterdir(), key=lambda p: p.stat().st_mtime if p.exists() else 0):
        if not entry.is_dir() or entry.name in keep:
            continue
        try:
            if entry.stat().st_mtime > deadline:
                continue
            size = folder_size(entry)
            shutil.rmtree(entry)
            removed += 1
            freed += size
        except OSError as exc:
            print(f"  не удалось удалить {entry}: {exc}", file=sys.stderr)
    if removed:
        print(f"{label}: удалено папок {removed}, освобождено {human(freed)}")
    return removed, freed


def sweep_stems(results: Path, max_age_days: float, keep: set[str] = frozenset()) -> tuple[int, int]:
    """
    Удалить только партии внутри результатов, сам разбор оставить.

    Аккорды, табы и MIDI весят килобайты и нужны пользователю; партии в
    WAV весят сотни мегабайт и пересоздаются заново при желании.
    """
    if not results.is_dir():
        return 0, 0
    deadline = time.time() - max_age_days * 86400
    removed = freed = 0
    for job_dir in results.iterdir():
        if job_dir.name in keep:
            continue
        stems = job_dir / "stems"
        if not stems.is_dir():
            continue
        try:
            if stems.stat().st_mtime > deadline:
                continue
            size = folder_size(stems)
            shutil.rmtree(stems)
            removed += 1
            freed += size
        except OSError as exc:
            print(f"  не удалось удалить {stems}: {exc}", file=sys.stderr)
    if removed:
        print(f"Партии: удалено наборов {removed}, освобождено {human(freed)}")
    return removed, freed


def main() -> int:
    if not DATA_DIR.is_dir():
        print(f"Папка данных не найдена: {DATA_DIR}", file=sys.stderr)
        return 1

    before = free_ratio(DATA_DIR)
    print(f"Свободно на диске: {before * 100:.0f}%")

    keep_uploads, keep_stems, keep_studio = KEEP_UPLOADS_DAYS, KEEP_STEMS_DAYS, KEEP_STUDIO_DAYS
    if before < LOW_DISK_RATIO:
        # Места мало -- режем сроки вчетверо, иначе сервис встанет
        keep_uploads = max(1.0, keep_uploads / 4)
        keep_stems = max(1.0, keep_stems / 4)
        keep_studio = max(7.0, keep_studio / 4)
        print(f"Мало места, сокращаю сроки до {keep_uploads:.0f}, {keep_stems:.0f} и {keep_studio:.0f} дней")
    # База не прочиталась -- не знаем, что в избранном: лучше ничего не удалять
    try:
        starred = favorites(DATA_DIR)
    except sqlite3.Error:
        return 1
    if starred:
        print(f"В избранном треков: {len(starred)} -- их не трогаем")

    sweep(DATA_DIR / "uploads", keep_uploads, "Загрузки", starred)
    sweep_stems(DATA_DIR / "results", keep_stems, starred)
    # Студия: исходник и результаты GPU-обработки (переделки, стемы).
    sweep(DATA_DIR / "studio", keep_studio, "Студия", starred)

    after = free_ratio(DATA_DIR)
    print(f"Стало свободно: {after * 100:.0f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
