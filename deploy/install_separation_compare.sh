#!/usr/bin/env bash
# Установка audio-separator для ЗАМЕРА -- сравнить, даёт ли каскад
# RoFormer -> Demucs лучшую гитару, чем один Demucs. Это не постоянная
# часть сервиса, а разовый инструмент: результат решает, стоит ли вообще
# заводить каскад в проде.
#
# ТРЕБУЕТСЯ, ЧТОБЫ УЖЕ БЫЛ УСТАНОВЛЕН install_separation.sh -- он ставит
# CPU-сборку PyTorch через отдельный индекс. audio-separator тоже просит
# torch, но версии достаточно широкой (>=2.3,<3), поэтому pip увидит уже
# стоящий CPU-torch и качать заново его не станет. Если сначала поставить
# audio-separator, а потом install_separation.sh -- порядок неважен, но
# earlier= надёжнее: меньше риска, что pip решит подтянуть версию с CUDA.
#
# Запускать от root:  bash install_separation_compare.sh
set -euo pipefail

VENV="${VENV:-/opt/nasluh/venv}"
NEED_GB=3

echo "=== Свободное место ==="
FREE_KB="$(df --output=avail / | tail -1)"
FREE_GB=$((FREE_KB / 1024 / 1024))
echo "  Свободно: ${FREE_GB} ГБ, нужно не меньше ${NEED_GB} ГБ"
echo "  (сам пакет лёгкий; веса модели RoFormer -- ещё около 200-400 МБ --"
echo "   скачаются при первом запуске замера, не сейчас)"
if [ "$FREE_GB" -lt "$NEED_GB" ]; then
    echo
    echo "Места мало. Освободите его:"
    echo "  $VENV/bin/pip cache purge"
    echo "  rm -rf /root/.cache/pip /tmp/pip-*"
    echo "  systemctl start nasluh-cleanup.service"
    exit 1
fi

echo
echo "=== Проверка: CPU-torch уже стоит? ==="
if "$VENV/bin/python" -c "import torch; exit(0 if not torch.cuda.is_available() else 1)" 2>/dev/null; then
    echo "  Torch на процессоре уже установлен -- второй раз качать не будет."
else
    echo "  Torch не найден или собран под видеокарту."
    echo "  Сначала выполните: bash install_separation.sh"
    exit 1
fi

echo
echo "=== audio-separator (для RoFormer) ==="
# Экстра [cpu] тянет onnxruntime без CUDA -- часть моделей UVR устроена
# на ONNX, а не на чистом PyTorch.
"$VENV/bin/pip" install --no-cache-dir "audio-separator[cpu]"

echo
echo "=== Проверка ==="
"$VENV/bin/python" - <<'PY'
from audio_separator.separator import Separator
import torch

print(f"torch {torch.__version__}, устройство: "
      f"{'видеокарта' if torch.cuda.is_available() else 'процессор'}")
separator = Separator(info_only=True)
print("audio-separator готов к работе")
PY

echo
df -h / | tail -1
echo
echo "Готово. Замер запускается вручную, отдельно от сервиса:"
echo "  $VENV/bin/python /opt/nasluh/app/scripts/compare_separation.py песня.mp3"
echo
echo "Первый запуск дольше остальных -- скачивает веса модели RoFormer."
echo "Дальше они лежат в ~/.cache/audio-separator-models и переиспользуются."
