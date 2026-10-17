# syntax=docker/dockerfile:1.7
# Замер ACE-Step 1.5 на русском (scripts/acestep_eval.py): наш GPU-воркер
# плюс то, чего в нём нет, -- XL-модель (4B, апрель 2026) и языковая модель-
# «планировщик» 4B. В рабочем воркере песня с нуля шла БЕЗ планировщика
# (ACESTEP_INIT_LLM=false, thinking=false) -- 05.10 владелец услышал «сбился
# такт»; здесь проверяем, что меняют планировщик и XL.
#
# Веса XL и 4B (~17 ГБ) качаются при старте воркера, а не лежат в образе:
# образ воркера и так ~30 ГБ, на раннере GitHub не хватит места на сборку.
# Для замера холодный старт подольше -- не страшно. Лицензия ACE-Step -- MIT.
FROM ghcr.io/antonsergeevich/naslux-worker:latest

ENV ACESTEP_CONFIG_PATH=acestep-v15-xl-sft ACESTEP_CONFIG_PATH2=acestep-v15-sft \
    ACESTEP_INIT_LLM=true ACESTEP_LM_MODEL_PATH=acestep-5Hz-lm-4B ACESTEP_LM_BACKEND=vllm

COPY start.sh /app/start.sh
RUN chmod +x /app/start.sh && python -m py_compile /app/handler.py
CMD ["/app/start.sh"]
