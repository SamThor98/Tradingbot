# Kronos inference microservice image.
# Heavy ML deps (torch + HF weights) are isolated here, away from the main app.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/models \
    KRONOS_MODEL_ID=NeoQuasar/Kronos-small \
    KRONOS_TOKENIZER_ID=NeoQuasar/Kronos-Tokenizer-base \
    KRONOS_DEVICE=cpu

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

# CPU-only torch first (smaller, no CUDA), then the rest.
RUN pip install --index-url https://download.pytorch.org/whl/cpu "torch>=2.2,<3"
COPY requirements.txt .
RUN pip install -r requirements.txt

# Vendor the Kronos model package at a pinned ref. Override KRONOS_REF with a
# commit SHA for reproducible builds (recommended for production).
ARG KRONOS_REF=master
RUN git clone https://github.com/shiyu-coder/Kronos.git /tmp/kronos \
    && git -C /tmp/kronos checkout "${KRONOS_REF}" \
    && cp -r /tmp/kronos/model /app/model \
    && rm -rf /tmp/kronos

COPY app.py .

# Pre-download weights into the image so cold starts don't pay the HF download.
# Tolerate failure (e.g. offline build) — the service downloads on first use.
RUN python -c "from huggingface_hub import snapshot_download; import os; snapshot_download(os.environ['KRONOS_MODEL_ID']); snapshot_download(os.environ['KRONOS_TOKENIZER_ID'])" || true

EXPOSE 8100
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8100"]
