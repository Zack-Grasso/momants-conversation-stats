FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --default-timeout=300 \
    torch==2.5.1 \
    --index-url https://download.pytorch.org/whl/cpu \
 && pip install --no-cache-dir --default-timeout=300 -r requirements.txt

COPY app ./app
COPY templates ./templates
COPY scripts ./scripts
COPY crontab ./crontab
COPY alembic.ini .
COPY alembic ./alembic

RUN chmod +x /app/scripts/scheduler-loop.sh

ENV HF_HOME=/app/.cache/huggingface
ENV TRANSFORMERS_CACHE=/app/.cache/huggingface/hub
ENV HF_HUB_DISABLE_TELEMETRY=1

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
