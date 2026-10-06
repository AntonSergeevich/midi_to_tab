# syntax=docker/dockerfile:1.7
# Проба YuE2-3B (M-A-P) на RunPod Serverless -- ТОЛЬКО ДЛЯ ВЛАДЕЛЬЦА САЙТА:
# веса YuE2, SheetSage2 и MERT-v2 под CC BY-NC 4.0 (некоммерческая).
# Покупателям сайта не показывается, пока у NASLUX нет коммерческой лицензии.
#
#   /venv     -- YuE2 (песня по тексту и стилю, кавер по нотам мелодии)
#                + Qwen3-ASR-1.7B (Apache 2.0): слова из записи, русский есть;
#   /venv-ss  -- SheetSage2: запись -> ноты мелодии (ABC), аккорды, тональность,
#                структура (куплет/припев). Свой torch 2.8 и transformers 4.45 --
#                с YuE2 (torch 2.10, transformers 4.57) в одном окружении не живёт.
# Собирается в GitHub Actions (.github/workflows/yue2-image.yml).
ARG CUDA_VERSION=12.8.1
FROM nvidia/cuda:${CUDA_VERSION}-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive LANG=C.UTF-8 LC_ALL=C.UTF-8 PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        software-properties-common curl ffmpeg libsndfile1 sox \
    && add-apt-repository ppa:deadsnakes/ppa && apt-get update \
    && apt-get install -y --no-install-recommends python3.11 python3.11-venv python3.11-dev build-essential \
    && rm -rf /var/lib/apt/lists/*

# YuE2 + Qwen3-ASR. qwen-asr ставится без зависимостей: он требует
# accelerate 1.12, а YuE2 -- 1.13; остальное (кроме gradio/flask) -- руками.
RUN python3.11 -m venv /venv \
    && /venv/bin/pip install --no-cache-dir torch==2.10.0 --index-url https://download.pytorch.org/whl/cu128 \
    && /venv/bin/pip install --no-cache-dir huggingface-hub==0.36.2 hf_transfer runpod==1.8.1 \
    && /venv/bin/python -c "from huggingface_hub import hf_hub_download as d; d('m-a-p/YuE2-3B', 'yue2_infer-0.1.5-py3-none-any.whl', local_dir='/tmp')" \
    && /venv/bin/pip install --no-cache-dir /tmp/yue2_infer-0.1.5-py3-none-any.whl && rm /tmp/*.whl \
    && /venv/bin/pip install --no-cache-dir --no-deps qwen-asr==0.0.6 \
    && /venv/bin/pip install --no-cache-dir nagisa==0.2.11 soynlp==0.0.493 qwen-omni-utils librosa sox pytz

# SheetSage2 -- по её requirements.txt
RUN python3.11 -m venv /venv-ss \
    && /venv-ss/bin/pip install --no-cache-dir torch==2.8.0 torchaudio==2.8.0 --index-url https://download.pytorch.org/whl/cu126 \
    && /venv-ss/bin/pip install --no-cache-dir transformers==4.45.2 huggingface-hub==0.36.0 safetensors==0.5.3 \
         numpy==1.24.3 scipy==1.13.1 mir_eval==0.8.2 pretty_midi==0.2.10 mido==1.3.3 setuptools==78.1.1 hf_transfer

# Веса -- в образ: холодный старт без скачивания ~15 ГБ. MERT-v2 -- той
# ревизии, к которой привязана SheetSage2 (base_model_revision в её
# config.json): офлайн она ищет именно её, а не текущую main.
ENV HF_HUB_ENABLE_HF_TRANSFER=1
RUN /venv/bin/python -c "from huggingface_hub import snapshot_download as s; \
s('m-a-p/YuE2-3B', local_dir='/models/YuE2-3B', allow_patterns=['*.json','*.py','*.safetensors','*.tiktoken','LICENSE','licenses/*']); \
s('m-a-p/YuE2-Vae', local_dir='/models/YuE2-Vae', allow_patterns=['*.json','*.py','*.safetensors','LICENSE','licenses/*']); \
s('Qwen/Qwen3-ASR-1.7B', local_dir='/models/Qwen3-ASR-1.7B')" \
    && HF_HOME=/models/hf /venv-ss/bin/python -c "from huggingface_hub import snapshot_download as s; \
s('m-a-p/SheetSage2', local_dir='/models/SheetSage2'); \
s('m-a-p/MERT-v2-FullSong', revision='d8ba1c745e733b3908ce6ad16ebeb17ac7600a42')" \
    && du -sh /models/* && rm -rf /root/.cache

ENV HF_HOME=/models/hf HF_HUB_OFFLINE=1
# abc2midi: ноты плана песни (ABC) -> MIDI. Отдельным слоем в конце, чтобы
# не пересобирать слои с весами
RUN apt-get update && apt-get install -y --no-install-recommends abcmidi && rm -rf /var/lib/apt/lists/*
COPY handler.py ss_run.py /app/
RUN /venv/bin/python -m py_compile /app/handler.py && /venv-ss/bin/python -m py_compile /app/ss_run.py \
    && /venv/bin/python -c "import yue2, qwen_asr, runpod, torch; print(torch.__version__)"
CMD ["/venv/bin/python", "-u", "/app/handler.py"]
