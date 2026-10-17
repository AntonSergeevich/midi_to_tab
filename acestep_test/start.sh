#!/usr/bin/env bash
# Докачать XL и планировщик 4B (если на этом диске их ещё нет) -- и в обработчик
set -euo pipefail
cd /app/ace
python - <<'PY'
from acestep.model_downloader import download_submodel as get
for name in ("acestep-v15-xl-sft", "acestep-5Hz-lm-4B"):
    print(name, get(name, prefer_source="huggingface"), flush=True)
PY
du -sh /app/checkpoints/* || true
exec python -u /app/handler.py
