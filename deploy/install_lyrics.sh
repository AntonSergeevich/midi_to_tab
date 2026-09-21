#!/usr/bin/env bash
# Установка распознавания текста песен.
#
# faster-whisper тянет только CTranslate2 и onnxruntime -- ни TensorFlow,
# ни драйверов видеокарты. Сами веса модели скачиваются при ПЕРВОМ разборе
# (small -- около 480 МБ) и кладутся в папку данных, а не в домашнюю: под
# systemd с ProtectSystem=strict домашней папки у службы попросту нет.
#
# Запускать от root:  bash install_lyrics.sh
set -euo pipefail

VENV="${VENV:-/opt/nasluh/venv}"
APP_DIR="${APP_DIR:-/opt/nasluh/app}"
DATA_DIR="${DATA_DIR:-/opt/nasluh/data}"
NEED_GB=2

echo "=== Свободное место ==="
FREE_KB="$(df --output=avail / | tail -1)"
FREE_GB=$((FREE_KB / 1024 / 1024))
echo "  Свободно: ${FREE_GB} ГБ, нужно не меньше ${NEED_GB} ГБ (веса модели ~0.5 ГБ)"
if [ "$FREE_GB" -lt "$NEED_GB" ]; then
    echo
    echo "Места мало. Освободите его:"
    echo "  $VENV/bin/pip cache purge"
    echo "  rm -rf /root/.cache/pip /tmp/pip-*"
    echo "  systemctl start nasluh-cleanup.service"
    exit 1
fi

echo
echo "=== faster-whisper ==="
"$VENV/bin/pip" install --no-cache-dir -r "$APP_DIR/requirements-lyrics.txt"

echo
echo "=== Папка для весов ==="
mkdir -p "$DATA_DIR/models"
chown -R nasluh:nasluh "$DATA_DIR/models"

echo
echo "=== Проверка ==="
"$VENV/bin/python" - <<'PY'
import sys
sys.path.insert(0, "/opt/nasluh/app")
from midi2tab import lyrics
ok, why = lyrics.available()
print("распознавание текста готово" if ok else why)
PY

echo
df -h / | tail -1
echo
echo "Готово. Перезапустите службу:  systemctl restart nasluh"
echo "Первый разбор текста будет дольше: скачиваются веса модели."
