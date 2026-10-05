# syntax=docker/dockerfile:1.7
# Проба YuE2-3B (M-A-P) на RunPod Serverless: песня с нуля по тексту и стилю.
# ТОЛЬКО ДЛЯ ОЦЕНКИ КАЧЕСТВА: веса под CC BY-NC 4.0 (некоммерческая) --
# на сайт не подключаются, пока у NASLUX нет коммерческой лицензии M-A-P.
# Собирается в GitHub Actions (.github/workflows/yue2-image.yml).
ARG CUDA_VERSION=12.8.1
FROM nvidia/cuda:${CUDA_VERSION}-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive LANG=C.UTF-8 LC_ALL=C.UTF-8 PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        software-properties-common curl ffmpeg libsndfile1 \
    && add-apt-repository ppa:deadsnakes/ppa && apt-get update \
    && apt-get install -y --no-install-recommends python3.11 python3.11-venv \
    && rm -rf /var/lib/apt/lists/*

RUN python3.11 -m venv /venv
ENV PATH=/venv/bin:$PATH
# torch под CUDA 12.8 -- как и у основного воркера: хосты RunPod с драйвером 12.8/12.9
RUN pip install --no-cache-dir torch==2.10.0 --index-url https://download.pytorch.org/whl/cu128 \
    && pip install --no-cache-dir huggingface-hub==0.36.2 hf_transfer runpod==1.8.1

# Пакет инференса и веса -- в образ: холодный старт без скачивания 8 ГБ
ENV HF_HUB_ENABLE_HF_TRANSFER=1
RUN python -c "from huggingface_hub import hf_hub_download as d; print(d('m-a-p/YuE2-3B', 'yue2_infer-0.1.5-py3-none-any.whl', local_dir='/tmp'))" \
    && pip install --no-cache-dir /tmp/yue2_infer-0.1.5-py3-none-any.whl \
    && rm /tmp/yue2_infer-0.1.5-py3-none-any.whl \
    && python -c "from huggingface_hub import snapshot_download as s; \
s('m-a-p/YuE2-3B', local_dir='/models/YuE2-3B', allow_patterns=['*.json','*.py','*.safetensors','*.tiktoken','LICENSE','licenses/*']); \
s('m-a-p/YuE2-Vae', local_dir='/models/YuE2-Vae', allow_patterns=['*.json','*.py','*.safetensors','LICENSE','licenses/*'])" \
    && du -sh /models/* && rm -rf /root/.cache

COPY handler.py /app/handler.py
RUN python -m py_compile /app/handler.py && python -c "import yue2, runpod, torch; print(torch.__version__)"
CMD ["python", "-u", "/app/handler.py"]
