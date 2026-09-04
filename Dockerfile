FROM node:22-alpine AS web-build

WORKDIR /web
COPY apps/web/package.json apps/web/package-lock.json ./
RUN npm ci
COPY apps/web/ ./
RUN npm run build

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Install dependencies as root, then permanently drop privilege for application
# startup/runtime. UID/GID are stable so deployment volumes can grant only the
# specific write access Operly needs instead of making the control plane root.
RUN groupadd --gid 10001 operly \
    && useradd --uid 10001 --gid 10001 --create-home --shell /usr/sbin/nologin operly

# requirements.txt declares reviewed compatibility ranges; production installs the
# exact audited graph so a rebuild cannot silently pick a newly published release.
COPY requirements.txt requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock && pip check

COPY . .
COPY --from=web-build /web/dist /app/apps/web/dist

# The source logo is intentionally kept lossless in the repository. Production
# only renders it at UI/social-preview sizes, so ship a bounded optimized copy
# into the canonical React bundle.
RUN python apps/web/scripts/optimize_logo.py \
    apps/web/public/operly-logo.png \
    apps/web/dist/operly-logo.png \
    && chown -R operly:operly /app

USER 10001:10001

EXPOSE 8000

# Bring a fresh/copy deployment to the current schema before starting the API.
# This keeps production's strict schema check while making Railway copies bootable.
CMD ["sh", "-c", "alembic upgrade head && uvicorn apps.api.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
