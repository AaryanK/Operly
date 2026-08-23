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

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
COPY --from=web-build /web/dist /app/apps/web/dist

EXPOSE 8000

# Bring a fresh/copy deployment to the current schema before starting the API.
# This keeps production's strict schema check while making Railway copies bootable.
CMD ["sh", "-c", "alembic upgrade head && uvicorn apps.api.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
