# MQTT Integration Guide

This system is designed so every controller, sensor board, and actuator communicates exclusively through MQTT. The FastAPI service stays stateless by subscribing to MQTT topics and exposing the most recent synchronized snapshot through `/api/status`.

## Goals

- **Single source of truth** for telemetry, flow/ebb state, and actuator state.
- **Consistent synchronization** using retained messages and QoS so new devices immediately know the current system state.
- **Reliable routing** with predictable topic patterns and device-specific payloads.

---

## Broker Setup (Mosquitto)

The `docker-compose.yml` file launches a Mosquitto broker at `mqtt-broker:1881`. To run locally:

```bash
docker compose up --build
```

The broker uses `config/mosquitto.conf`. For production, enable authentication and TLS.

### Recommended broker hardening

1. **Disable anonymous access** and add user credentials.
2. **Enable persistence** so retained topics survive restarts.
3. **Set `max_inflight_messages`, `message_size_limit`, and `max_queued_messages`** based on device constraints.

Example (secure) Mosquitto configuration:

```conf
persistence true
persistence_location /mosquitto/data/

listener 8883
allow_anonymous false
password_file /mosquitto/config/passwords
cafile /mosquitto/config/ca.crt
certfile /mosquitto/config/server.crt
keyfile /mosquitto/config/server.key
```

---

## Hydromatic MQTT Environment Variables

These are read by the FastAPI service:

| Variable | Default | Purpose |
| --- | --- | --- |
| `MQTT_ENABLED` | `false` | Enable MQTT subscriber loop. |
| `MQTT_BROKER_HOST` | `mqtt-broker` | MQTT broker host. |
| `MQTT_BROKER_PORT` | `1881` | MQTT broker port. |
| `MQTT_USERNAME` | unset | Username for broker auth. |
| `MQTT_PASSWORD` | unset | Password for broker auth. |
| `MQTT_TLS` | `false` | Enable TLS when `true`. |
| `MQTT_CLIENT_ID` | `hydromatic-server` | MQTT client ID. |
| `MQTT_KEEPALIVE` | `30` | MQTT keepalive seconds. |
| `MQTT_TOPIC_PREFIX` | `hydromatic` | Base topic prefix. |
| `MQTT_SITE_ID` | `primary` | Site identifier for multi-site deployments. |

---

## Topic Schema (Routing Plan)

All topics are rooted at:

```
{MQTT_TOPIC_PREFIX}/v1/site/{SITE_ID}
```

### Device telemetry (publish)

```
.../device/{DEVICE_ID}/telemetry
```

**QoS:** 1
**Retain:** optional (recommended for critical sensors with slow update cadence)

Payload (JSON):

```json
{
  "environment": {
    "temperature_c": 22.8,
    "humidity_percent": 61.2,
    "co2_ppm": 640,
    "ph": 6.0,
    "ec": 1.9
  },
  "reservoir": {
    "volume_liters": 40.5,
    "water_level_percent": 72,
    "pump_state": "flow"
  }
}
```

### Device state (publish)

```
.../device/{DEVICE_ID}/state
```

Use this topic for **actuator state**, **heartbeat info**, and device diagnostics.

```json
{
  "status": "online",
  "firmware": "v2.1.0",
  "last_cycle_start": "2024-07-01T04:12:00Z",
  "uptime_seconds": 392002
}
```

### Flow/Ebb synchronization (publish + retain)

```
.../system/flow
```

**QoS:** 1
**Retain:** **true**

Payload (JSON):

```json
{
  "phase": "ebb",
  "phase_remaining_seconds": 420,
  "last_cycle_start": "2024-07-01T04:12:00Z",
  "expected_next_phase": "flow"
}
```

### Global alerts (publish + retain)

```
.../system/alerts
```

**QoS:** 1
**Retain:** true

```json
{
  "alerts": [
    {"severity": "warning", "message": "Reservoir level low", "source": "level-sensor"}
  ]
}
```

### Camera metadata (publish)

```
.../system/camera
```

```json
{
  "stream": "rtsp://10.0.0.50:8554/hydromatic",
  "last_snapshot": "2024-07-01T04:11:30Z"
}
```

---

## Device Provisioning Checklist

1. **Assign a unique `DEVICE_ID`** (printed label + firmware config).
2. **Configure the MQTT broker hostname and credentials**.
3. **Publish retained LWT:**
   - Topic: `.../device/{DEVICE_ID}/state`
   - Payload: `{ "status": "offline" }`
4. **Publish telemetry at a steady interval** (5–30 seconds for fast sensors).
5. **Retain flow state** so late-joining devices instantly synchronize.
6. **Use QoS 1 for critical readings** (water level, pump state, flow phase).

---

## Reliability & Synchronization Strategy

- **Retained flow state** keeps every device aligned on the current ebb/flow phase.
- **Device state topics** provide heartbeat and diagnostics. Any device missing for >2x telemetry interval should be flagged.
- **QoS 1** ensures delivery at least once; devices must be idempotent.
- **Time synchronization**: all devices should use UTC (NTP or RTC) so timestamps align.
- **Backpressure**: throttle telemetry to avoid broker overload (especially with Wi-Fi).
- **Persistent storage**: the broker retains state across power cycles, so control resumes quickly.

---

## Example Publisher (Python)

```python
import json
import time
import paho.mqtt.client as mqtt

client = mqtt.Client(client_id="reservoir-controller")
client.connect("127.0.0.1", 1881)

while True:
    payload = {
        "reservoir": {
            "volume_liters": 39.8,
            "water_level_percent": 70,
            "pump_state": "flow",
        }
    }
    client.publish(
        "hydromatic/v1/site/primary/device/reservoir-controller/telemetry",
        json.dumps(payload),
        qos=1,
        retain=False,
    )
    time.sleep(10)
```

---

## API Consumer Behavior

The FastAPI service subscribes to the topics above and exposes the latest state in `/api/status`:

- `environment`, `reservoir`, and `lighting` are merged from device telemetry.
- `flow` is updated from `system/flow` and kept in sync for all devices.
- `alerts` and `camera` propagate to the dashboard.
- `devices` includes last-seen metadata for per-device monitoring.

---

## Operational Runbook

- **New device onboarding:** add firmware config → publish retained LWT → publish telemetry.
- **Controller takeover:** subscribe to `.../system/flow` and `.../system/alerts` (retained).
- **Network outage:** broker persistence restores retained state; devices resubscribe on reconnect.
- **Flow/ebb reliability:** prefer a dedicated flow controller publishing `system/flow` so every pump/valve acts on the same phase.
