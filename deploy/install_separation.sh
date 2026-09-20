#!/usr/bin/env bash
# Установка разделения на партии БЕЗ драйверов видеокарты.
#
# Обычный "pip install demucs" на Linux тянет torch со всем набором CUDA
# -- около двух гигабайт для видеокарты, которой на сервере обычно нет.
# Сервер с диском на 40 ГБ это переполняет.
#
# Запускать от root:  bash install_separation.sh
set -euo pipefail

VENV="${VENV:-/opt/nasluh/venv}"
APP_DIR="${APP_DIR:-/opt/nasluh/app}"
NEED_GB=4

echo "=== Свободное место ==="
FREE_KB="$(df --output=avail / | tail -1)"
FREE_GB=$((FREE_KB / 1024 / 1024))
echo "  Свободно: ${FREE_GB} ГБ, нужно не меньше ${NEED_GB} ГБ"
if [ "$FREE_GB" -lt "$NEED_GB" ]; then
    echo
    echo "Места мало. Освободите его:"
    echo "  $VENV/bin/pip cache purge"
    echo "  rm -rf /root/.cache/pip /tmp/pip-*"
    echo "  systemctl start nasluh-cleanup.service"
    exit 1
fi

echo
echo "=== PyTorch для процессора ==="
# Отдельный индекс PyTorch отдаёт сборку без CUDA: около 200 МБ вместо
# двух с лишним гигабайт.
"$VENV/bin/pip" install --no-cache-dir torch \
    --index-url https://download.pytorch.org/whl/cpu

echo
echo "=== Demucs ==="
"$VENV/bin/pip" install --no-cache-dir -r "$APP_DIR/requirements-separation.txt"

echo
echo "=== Проверка ==="
"$VENV/bin/python" - <<'PY'
import torch, demucs.separate  # noqa: F401
cuda = torch.cuda.is_available()
print(f"torch {torch.__version__}, устройство: {'видеокарта' if cuda else 'процессор'}")
nvidia = [p for p in __import__('sys').path if 'nvidia' in p]
print("драйверы CUDA не установлены" if not cuda else "CUDA доступна")
PY

echo
df -h / | tail -1
echo
echo "Готово. Перезапустите службу:  systemctl restart nasluh"
