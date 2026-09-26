# Обложки NASLUX (handler.py): SDXL-Lightning 4 шага + офлайн-перевод ru->en.
# Веса внутри образа (~7 ГБ): у эндпоинта workersMin=0, в простое не платим.
FROM pytorch/pytorch:2.4.1-cuda12.1-cudnn9-runtime

ENV PYTHONUNBUFFERED=1 HF_HUB_ENABLE_HF_TRANSFER=1
RUN pip install --no-cache-dir runpod==1.8.1 diffusers==0.31.0 transformers==4.46.3 \
        accelerate==1.1.1 safetensors hf_transfer argostranslate==1.9.6

RUN python - <<'PY'
from huggingface_hub import hf_hub_download, snapshot_download
snapshot_download("stabilityai/stable-diffusion-xl-base-1.0", local_dir="/models/sdxl",
                  allow_patterns=["model_index.json", "scheduler/*", "tokenizer/*", "tokenizer_2/*",
                                  "text_encoder/config.json", "text_encoder/model.fp16.safetensors",
                                  "text_encoder_2/config.json", "text_encoder_2/model.fp16.safetensors",
                                  "unet/config.json", "vae/config.json"])
snapshot_download("madebyollin/sdxl-vae-fp16-fix", local_dir="/models/vae",
                  allow_patterns=["config.json", "diffusion_pytorch_model.safetensors"])
hf_hub_download("ByteDance/SDXL-Lightning", "sdxl_lightning_4step_unet.safetensors", local_dir="/models")
PY
RUN python -c "import argostranslate.package as p; p.update_package_index(); \
pkg = next(x for x in p.get_available_packages() if x.from_code == 'ru' and x.to_code == 'en'); \
p.install_from_path(pkg.download()); import argostranslate.translate as t; print(t.translate('Земля в иллюминаторе', 'ru', 'en'))" \
    && rm -rf /root/.cache/huggingface && du -sh /models/* /root/.local/share/argos-translate

COPY handler.py /handler.py
RUN python -m py_compile /handler.py
CMD ["python", "-u", "/handler.py"]
