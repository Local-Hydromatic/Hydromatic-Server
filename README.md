# Hydromatic-Server

Local-first control center for a hydroponic system. This repository now includes a lightweight FastAPI service and a static dashboard suitable for running inside a Docker container on embedded hardware like the Luckfox Pico-Pro (RV1106).

## Quick start

```bash
docker compose up --build
```

Then open `http://localhost:8080` to see the dashboard.

## What’s included

- **FastAPI backend** (`app/main.py`) with MQTT-driven telemetry ingestion.
- **Static web UI** (`web/index.html`) that pulls live status from the API.
- **Docker support** (`Dockerfile`, `docker-compose.yml`) for local or embedded deployment.
- **MQTT integration guide** (`docs/mqtt.md`) for topic routing, reliability, and device onboarding.

## Embedded notes (Luckfox Pico-Pro / RV1106)

- The base image is `python:3.11-slim`, which supports multi-arch builds.
- For camera access, uncomment the `devices` and `privileged` settings in `docker-compose.yml` and map the correct `/dev/video*` device.
- Extend `app/mqtt.py` handlers with sensor polling, alerting, and RTSP snapshot logic as needed.

## API

- `GET /api/health` — basic health check.
- `GET /api/status` — example telemetry payload used by the UI.
