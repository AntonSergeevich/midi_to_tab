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

import os
import shutil
import sys
import time
from pathlib import Path

DATA_DIR = Path(os.environ.get("MIDI2TAB_DATA", "/opt/nasluh/data"))
KEEP_UPLOADS_DAYS = float(os.environ.get("NASLUH_KEEP_UPLOADS_DAYS", "14"))
KEEP_STEMS_DAYS = float(os.environ.get("NASLUH_KEEP_STEMS_DAYS", "14"))
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


def sweep(root: Path, max_age_days: float, label: str) -> tuple[int, int]:
    """Удалить папки старше срока. Возвращает (сколько, сколько байт)."""
    if not root.is_dir():
        return 0, 0
    deadline = time.time() - max_age_days * 86400
    removed = freed = 0
    for entry in sorted(root.iterdir(), key=lambda p: p.stat().st_mtime if p.exists() else 0):
        if not entry.is_dir():
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


def sweep_stems(results: Path, max_age_days: float) -> tuple[int, int]:
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

    keep_uploads, keep_stems = KEEP_UPLOADS_DAYS, KEEP_STEMS_DAYS
    if before < LOW_DISK_RATIO:
        # Места мало -- режем сроки вчетверо, иначе сервис встанет
        keep_uploads = max(1.0, keep_uploads / 4)
        keep_stems = max(1.0, keep_stems / 4)
        print(f"Мало места, сокращаю сроки до {keep_uploads:.0f} и {keep_stems:.0f} дней")

    sweep(DATA_DIR / "uploads", keep_uploads, "Загрузки")
    sweep_stems(DATA_DIR / "results", keep_stems)

    after = free_ratio(DATA_DIR)
    print(f"Стало свободно: {after * 100:.0f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
