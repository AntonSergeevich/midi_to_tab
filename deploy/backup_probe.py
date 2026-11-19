"""Найти в архиве бэкапа папку данных NASLUX и её размер -- не распаковывая.

    python3 -I deploy/backup_probe.py /root/backup_naslux.tar
    -> «opt/nasluh/data 1234567» (папка в архиве и размер в КБ без моделей)

Бэкап Beget может быть снимком всего сервера (19 ГБ у владельца, 19.11),
а на диске меньше: распаковывать целиком нельзя. Заголовки tar читаются с
пропуском содержимого (seek), поэтому даже большой архив проходится быстро.
"""
from __future__ import annotations

import sys
import tarfile
import zipfile


def members(path: str):
    """(имя, размер) всех файлов архива."""
    if path.lower().endswith(".zip"):
        with zipfile.ZipFile(path) as archive:
            for info in archive.infolist():
                yield info.filename.rstrip("/"), info.file_size
        return
    with tarfile.open(path) as archive:
        for info in archive:
            yield info.name.lstrip("./"), info.size if info.isfile() else 0


def probe(path: str) -> tuple[str, int]:
    sizes: dict[str, int] = {}
    dbs = []
    for name, size in members(path):
        sizes[name] = size
        if name == "app.db" or name.endswith("/app.db"):
            dbs.append(name)
    if not dbs:
        raise SystemExit("В архиве нет app.db -- это точно архив сервера NASLUX?")
    best = next((d for d in dbs if "nasluh/data/" in d or d.startswith("data/")), dbs[0])
    prefix = best.rsplit("/", 1)[0] if "/" in best else "."
    lead = "" if prefix == "." else prefix + "/"
    total = sum(size for name, size in sizes.items()
                if name.startswith(lead) and not name.startswith(lead + "models/"))
    return prefix, total // 1024 + 1


if __name__ == "__main__":
    folder, kb = probe(sys.argv[1])
    print(folder, kb)
