from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

APP_ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = APP_ROOT / "web"

app = FastAPI(title="Hydromatic Server")

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def root() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
def health_check() -> dict:
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}


@app.get("/api/status")
def system_status() -> JSONResponse:
    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "environment": {
            "temperature_c": 23.4,
            "humidity_percent": 58,
            "co2_ppm": 620,
            "ph": 6.1,
            "ec": 1.8,
        },
        "reservoir": {
            "volume_liters": 42,
            "water_level_percent": 76,
            "pump_state": "idle",
        },
        "lighting": {
            "mode": "auto",
            "intensity_percent": 72,
            "photoperiod": "18/6",
        },
        "alerts": [
            {
                "severity": "info",
                "message": "All systems nominal. Scheduled calibration in 14 days.",
            }
        ],
        "camera": {
            "stream": "rtsp://127.0.0.1:8554/hydromatic",
            "last_snapshot": "pending",
        },
    }
    return JSONResponse(payload)
