# syntax=docker/dockerfile:1.7

FROM node:22-bookworm-slim AS node-tools

FROM python:3.12-bookworm AS job
COPY --from=node-tools /usr/local/bin/node /usr/local/bin/node
COPY --from=node-tools /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
    && groupadd --gid 10001 operly \
    && useradd --uid 10001 --gid 10001 --create-home --shell /usr/sbin/nologin operly \
    && mkdir -p /workspace/frontend /workspace/backend /workspace/workers /workspace/tests /workspace/migrations \
    && chown -R 10001:10001 /workspace
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPYCACHEPREFIX=/tmp/pycache \
    PYTHONUNBUFFERED=1 \
    HOME=/home/operly \
    PATH=/workspace/.venv/bin:/usr/local/bin:/usr/bin:/bin
USER 10001:10001
WORKDIR /workspace
CMD ["sleep", "infinity"]

FROM python:3.12-slim-bookworm AS proxy
WORKDIR /opt/operly-proxy
COPY apps/runner/egress_proxy.py /opt/operly-proxy/egress_proxy.py
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER 65534:65534
ENTRYPOINT ["python", "/opt/operly-proxy/egress_proxy.py"]

FROM python:3.12-slim-bookworm AS gateway
WORKDIR /app
COPY apps/runner/requirements.txt /tmp/runner-requirements.txt
RUN pip install --no-cache-dir -r /tmp/runner-requirements.txt \
    && mkdir -p /var/lib/operly-runner
COPY apps/runner /app/apps/runner
COPY packages /app/packages
ENV PYTHONPATH=/app \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    OPERLY_RUNNER_STATE_DIR=/var/lib/operly-runner
EXPOSE 8080
CMD ["uvicorn", "apps.runner.main:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers", "--forwarded-allow-ips", "*"]
