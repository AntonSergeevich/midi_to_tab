# Ретранслятор запросов к Mureka (см. handler.py): ни моделей, ни GPU --
# маленький образ, быстрый холодный старт, работает на CPU-воркерах RunPod.
FROM python:3.11-slim
RUN pip install --no-cache-dir runpod==1.8.1
COPY handler.py /handler.py
RUN python -m py_compile /handler.py
CMD ["python", "-u", "/handler.py"]
