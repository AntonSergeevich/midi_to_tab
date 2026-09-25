# syntax=docker/dockerfile:1.7
# GPU-воркер NASLUX для RunPod Serverless: ACE-Step 1.5 (переделка трека в
# другой стиль, дописывание партий) и Demucs htdemucs_6s (разделение на
# партии). Собирается в GitHub Actions (.github/workflows/worker-image.yml),
# публикуется в ghcr.io.
#
# Веса моделей лежат ВНУТРИ образа: сетевой диск RunPod оплачивается и в
# простое, а у эндпоинта workersMin=0 -- без запросов не платим ничего.
# Цена этого -- холодный старт, пока RunPod скачивает образ.

ARG CUDA_VERSION=12.8.1
FROM nvidia/cuda:${CUDA_VERSION}-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive LANG=C.UTF-8 LC_ALL=C.UTF-8 \
    PYTHONUNBUFFERED=1 TOKENIZERS_PARALLELISM=false

RUN apt-get update && apt-get install -y --no-install-recommends \
        software-properties-common build-essential git curl ffmpeg libsndfile1 \
    && add-apt-repository ppa:deadsnakes/ppa && apt-get update \
    && apt-get install -y --no-install-recommends python3.11 python3.11-dev python3.11-venv \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.7 /uv /uvx /bin/

# ACE-Step закреплён на коммите: main меняется, а звук сервиса -- нет.
ARG ACESTEP_REF=ca1e85fe9430179831e6bc6be790c332190a3866
RUN git clone https://github.com/ace-step/ACE-Step-1.5 /app/ace \
    && git -C /app/ace checkout ${ACESTEP_REF}
WORKDIR /app/ace
RUN uv sync --frozen --no-dev --python python3.11 \
    && uv pip install --python .venv/bin/python runpod==1.8.1 demucs==4.1.0 \
    && rm -rf /root/.cache/uv
ENV PATH=/app/ace/.venv/bin:$PATH

# Веса: turbo -- быстрая переделка стиля (cover), base -- дописывание и
# вытаскивание партий (lego/complete/extract есть только в base). Качаем
# штатным загрузчиком ACE-Step и ПОЛНЫМ комплектом основной модели: если
# при старте сервера чего-то из него не найдётся (например, языковой
# модели), он сам докачает всё заново -- на каждом холодном старте.
ENV ACESTEP_CHECKPOINTS_DIR=/app/checkpoints HF_HUB_ENABLE_HF_TRANSFER=1
RUN uv pip install --python .venv/bin/python hf_transfer \
    && python -c "from acestep.model_downloader import download_main_model as m, download_submodel as s, check_main_model_exists as c; \
print(m(prefer_source='huggingface')); print(s('acestep-v15-base', prefer_source='huggingface')); assert c()" \
    && python -c "from demucs.pretrained import get_model; get_model('htdemucs_6s')" \
    && du -sh /app/checkpoints/* /root/.cache/*

# Обе модели грузятся при старте воркера, а не на первом запросе: иначе
# ACE-Step молча подменяет незагруженную вторую модель основной.
ENV ACESTEP_CONFIG_PATH=acestep-v15-turbo ACESTEP_CONFIG_PATH2=acestep-v15-base \
    ACESTEP_NO_INIT=false ACESTEP_INIT_LLM=false ACESTEP_API_PORT=8901

COPY handler.py /app/handler.py
RUN python -m py_compile /app/handler.py && echo ${ACESTEP_REF} > /app/ACESTEP_COMMIT
CMD ["python", "-u", "/app/handler.py"]
