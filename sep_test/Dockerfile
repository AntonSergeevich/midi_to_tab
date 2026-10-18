# syntax=docker/dockerfile:1.7
# Замер разделения на партии (scripts/sep_eval.py): наш GPU-воркер (Demucs
# htdemucs_6s) плюс BS-RoFormer -- SW на 6 партий и модель голоса
# Resurrection. Код bs-roformer-infer -- MIT, веса качаются по sha256.
# Лицензии самих весов (сообщество UVR/MVSEP) проверяются отдельно до
# того, как модель пойдёт на сайт.
FROM ghcr.io/antonsergeevich/naslux-worker:latest

ENV BS_ROFORMER_MODELS_PATH=/app/roformer
RUN uv pip install --python /app/ace/.venv/bin/python bs-roformer-infer==0.1.5 \
    && python -c "from bs_roformer.download import ensure_model_assets as get; \
get('roformer-model-bs-roformer-sw-by-jarredou'); get('roformer-model-bs-roformer-vocals-resurrection-by-unwa')" \
    && du -sh /app/roformer

COPY handler.py /app/sep_handler.py
RUN python -c "from demucs.pretrained import get_model; get_model('htdemucs_ft')" && python -m py_compile /app/sep_handler.py && python -c "import bs_roformer, demucs, runpod, soundfile"
CMD ["python", "-u", "/app/sep_handler.py"]
