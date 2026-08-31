FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends bash git curl ca-certificates procps \
    && rm -rf /var/lib/apt/lists/*

COPY apps/computer_runner/requirements.txt /tmp/computer-requirements.txt
RUN pip install -r /tmp/computer-requirements.txt \
    && playwright install --with-deps chromium

COPY apps /app/apps

EXPOSE 8092

CMD ["uvicorn", "apps.computer_runner.main:app", "--host", "0.0.0.0", "--port", "8092"]
