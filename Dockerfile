# Stage 1: Build Frontend
FROM node:22-alpine AS frontend-builder

WORKDIR /frontend
COPY frontend/package*.json ./
RUN npm install
COPY frontend/ ./
RUN npm run build

# Stage 2: Runtime Container (Python + NUT + supervisord)
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=UTC \
    WEB_PORT=8765 \
    NUT_PORT=3493 \
    WEBNUT_DATA_DIR=/app/data \
    WEBNUT_NUT_DIR=/etc/nut \
    WEBNUT_STATIC_DIR=/app/static \
    WEBNUT_SUPERVISOR_CONF=/etc/supervisord.conf

RUN apt-get update && apt-get install -y --no-install-recommends \
    nut \
    nut-server \
    nut-client \
    libnutscan2 \
    libusb-1.0-0 \
    usbutils \
    supervisor \
    iputils-ping \
    iproute2 \
    net-tools \
    curl \
    tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY backend/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY backend/app /app/app
COPY --from=frontend-builder /frontend/dist /app/static
COPY docker/supervisord.conf /etc/supervisord.conf
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh

RUN sed -i 's/\r$//' /usr/local/bin/entrypoint.sh \
    && chmod +x /usr/local/bin/entrypoint.sh \
    && mkdir -p /app/data /etc/nut /run/nut /var/run/nut

EXPOSE 8765 3493

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${WEB_PORT}/api/health" || exit 1

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
