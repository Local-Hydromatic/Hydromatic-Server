from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.mqtt import build_lifespan, create_mqtt_service

APP_ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = APP_ROOT / "web"

mqtt_service = create_mqtt_service()

app = FastAPI(title="Hydromatic Server", lifespan=build_lifespan(mqtt_service))

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def root() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
def health_check() -> dict:
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}


@app.get("/api/status")
def system_status() -> JSONResponse:
    return JSONResponse(mqtt_service.state.snapshot())
