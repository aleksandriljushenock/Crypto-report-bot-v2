FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DATA_DIR=/app/data \
    LOG_DIR=/app/logs

WORKDIR /app

RUN groupadd --system bot && useradd --system --gid bot --create-home bot

COPY requirements.txt ./
RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /app/data /app/logs \
    && chown -R bot:bot /app

USER bot

CMD ["python", "cloud_entrypoint.py"]
