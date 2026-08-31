FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    OPERLY_AGENT_COMPUTER_RUNNER_ROOT=/runtime

WORKDIR /app

# Reference coding/research workbench. Production uses the same Agent Computer
# protocol behind per-session container/microVM isolation rather than sharing
# this process namespace.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       bash \
       build-essential \
       ca-certificates \
       curl \
       file \
       git \
       jq \
       nodejs \
       npm \
       procps \
       ripgrep \
       unzip \
       wget \
       zip \
    && rm -rf /var/lib/apt/lists/*

COPY apps/computer_runner/requirements.txt /tmp/computer-requirements.txt
RUN pip install -r /tmp/computer-requirements.txt \
    && playwright install --with-deps chromium \
    && useradd --create-home --uid 10001 computer \
    && mkdir -p /runtime \
    && chown computer:computer /runtime \
    && chmod -R a+rX /ms-playwright

COPY apps /app/apps
RUN chown -R root:root /app && chmod -R a+rX /app

USER computer

EXPOSE 8092

CMD ["uvicorn", "apps.computer_runner.main:app", "--host", "0.0.0.0", "--port", "8092"]
