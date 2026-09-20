# Phase 20: one image that carries the dashboard. Stage 1 builds it, stage 2 is
# the API with the built files at /app/static, which the control plane serves at
# "/" -- so the whole product is one URL, with nothing to install or proxy.
FROM node:22-alpine AS dashboard
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.11-slim

WORKDIR /app

COPY backend/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --break-system-packages -r /app/requirements.txt

COPY backend/app /app/app
COPY --from=dashboard /web/dist /app/static

# The hosted compose runs as this user (10001). The directories are created and
# owned here so a fresh named volume mounted over them is writable without root.
# The development compose keeps running as root, so existing volumes still work.
RUN mkdir -p /data /oauth && chown 10001:10001 /data /oauth

ENV PYTHONUNBUFFERED=1
EXPOSE 8000

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
