import asyncio
import json
import logging
import os
import ssl
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional

from asyncio_mqtt import Client, MqttError
from paho.mqtt.client import topic_matches_sub

logger = logging.getLogger("hydromatic.mqtt")


@dataclass
class MQTTConfig:
    enabled: bool = False
    broker_host: str = "localhost"
    broker_port: int = 1883
    username: Optional[str] = None
    password: Optional[str] = None
    tls: bool = False
    client_id: str = "hydromatic-server"
    keepalive: int = 30
    topic_prefix: str = "hydromatic"
    site_id: str = "primary"


@dataclass
class MQTTState:
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    environment: Dict[str, Any] = field(
        default_factory=lambda: {
            "temperature_c": 23.4,
            "humidity_percent": 58,
            "co2_ppm": 620,
            "ph": 6.1,
            "ec": 1.8,
        }
    )
    reservoir: Dict[str, Any] = field(
        default_factory=lambda: {
            "volume_liters": 42,
            "water_level_percent": 76,
            "pump_state": "idle",
        }
    )
    lighting: Dict[str, Any] = field(
        default_factory=lambda: {
            "mode": "auto",
            "intensity_percent": 72,
            "photoperiod": "18/6",
        }
    )
    flow: Dict[str, Any] = field(
        default_factory=lambda: {
            "phase": "ebb",
            "phase_remaining_seconds": 0,
            "last_cycle_start": None,
        }
    )
    alerts: List[Dict[str, Any]] = field(
        default_factory=lambda: [
            {
                "severity": "info",
                "message": "All systems nominal. Scheduled calibration in 14 days.",
            }
        ]
    )
    camera: Dict[str, Any] = field(
        default_factory=lambda: {
            "stream": "rtsp://127.0.0.1:8554/hydromatic",
            "last_snapshot": "pending",
        }
    )
    devices: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    def snapshot(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "environment": self.environment,
            "reservoir": self.reservoir,
            "lighting": self.lighting,
            "flow": self.flow,
            "alerts": self.alerts,
            "camera": self.camera,
            "devices": self.devices,
        }

    def touch(self) -> None:
        self.timestamp = datetime.now(timezone.utc).isoformat()

    def update_device(self, device_id: str, payload: Dict[str, Any]) -> None:
        self.devices.setdefault(device_id, {})
        self.devices[device_id].update(payload)
        self.devices[device_id]["last_seen"] = self.timestamp

    def merge_payload(self, payload: Dict[str, Any]) -> None:
        self.environment.update(payload.get("environment", {}))
        self.reservoir.update(payload.get("reservoir", {}))
        self.lighting.update(payload.get("lighting", {}))
        self.camera.update(payload.get("camera", {}))
        if "flow" in payload:
            self.flow.update(payload["flow"])
        if "alerts" in payload:
            self.alerts = payload["alerts"]


@dataclass
class MQTTMessageRoute:
    subscription: str
    handler: Callable[[str, bytes], Awaitable[None]]


class MessageRouter:
    def __init__(self) -> None:
        self._routes: List[MQTTMessageRoute] = []

    def add_route(self, subscription: str, handler: Callable[[str, bytes], Awaitable[None]]) -> None:
        self._routes.append(MQTTMessageRoute(subscription=subscription, handler=handler))

    @property
    def subscriptions(self) -> List[str]:
        return [route.subscription for route in self._routes]

    async def dispatch(self, topic: str, payload: bytes) -> None:
        for route in self._routes:
            if topic_matches_sub(route.subscription, topic):
                await route.handler(topic, payload)


class HydromaticMQTTService:
    def __init__(self, config: MQTTConfig) -> None:
        self.config = config
        self.state = MQTTState()
        self.router = MessageRouter()
        self._task: Optional[asyncio.Task[None]] = None
        self._configure_routes()

    @property
    def enabled(self) -> bool:
        return self.config.enabled

    def _configure_routes(self) -> None:
        base = f"{self.config.topic_prefix}/v1/site/{self.config.site_id}"
        self.router.add_route(f"{base}/device/+/telemetry", self._handle_device_telemetry)
        self.router.add_route(f"{base}/device/+/state", self._handle_device_state)
        self.router.add_route(f"{base}/system/flow", self._handle_flow_state)
        self.router.add_route(f"{base}/system/alerts", self._handle_alerts)
        self.router.add_route(f"{base}/system/camera", self._handle_camera)

    async def _handle_device_telemetry(self, topic: str, payload: bytes) -> None:
        message = self._decode_payload(payload)
        if message is None:
            return
        self.state.touch()
        device_id = self._device_id_from_topic(topic)
        if device_id:
            self.state.update_device(device_id, message)
        self.state.merge_payload(message)

    async def _handle_device_state(self, topic: str, payload: bytes) -> None:
        message = self._decode_payload(payload)
        if message is None:
            return
        self.state.touch()
        device_id = self._device_id_from_topic(topic)
        if device_id:
            self.state.update_device(device_id, message)
        self.state.merge_payload(message)

    async def _handle_flow_state(self, topic: str, payload: bytes) -> None:
        message = self._decode_payload(payload)
        if message is None:
            return
        self.state.touch()
        self.state.flow.update(message)

    async def _handle_alerts(self, topic: str, payload: bytes) -> None:
        message = self._decode_payload(payload)
        if message is None:
            return
        self.state.touch()
        if isinstance(message.get("alerts"), list):
            self.state.alerts = message["alerts"]

    async def _handle_camera(self, topic: str, payload: bytes) -> None:
        message = self._decode_payload(payload)
        if message is None:
            return
        self.state.touch()
        self.state.camera.update(message)

    def _decode_payload(self, payload: bytes) -> Optional[Dict[str, Any]]:
        try:
            decoded = json.loads(payload.decode("utf-8"))
        except json.JSONDecodeError:
            logger.warning("MQTT payload is not valid JSON")
            return None
        if not isinstance(decoded, dict):
            logger.warning("MQTT payload should be a JSON object")
            return None
        return decoded

    def _device_id_from_topic(self, topic: str) -> Optional[str]:
        parts = topic.split("/")
        if "device" in parts:
            index = parts.index("device")
            if index + 1 < len(parts):
                return parts[index + 1]
        return None

    async def start(self) -> None:
        if not self.enabled:
            return
        if self._task is None:
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run(self) -> None:
        backoff = 1
        while True:
            try:
                await self._run_session()
                backoff = 1
            except MqttError as error:
                logger.warning("MQTT connection error: %s", error)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30)

    async def _run_session(self) -> None:
        tls_context = ssl.create_default_context() if self.config.tls else None
        async with Client(
            hostname=self.config.broker_host,
            port=self.config.broker_port,
            username=self.config.username,
            password=self.config.password,
            client_id=self.config.client_id,
            keepalive=self.config.keepalive,
            tls_context=tls_context,
        ) as client:
            async with client.unfiltered_messages() as messages:
                for subscription in self.router.subscriptions:
                    await client.subscribe(subscription, qos=1)
                async for message in messages:
                    await self.router.dispatch(message.topic, message.payload)


def load_mqtt_config() -> MQTTConfig:
    return MQTTConfig(
        enabled=os.getenv("MQTT_ENABLED", "false").lower() == "true",
        broker_host=os.getenv("MQTT_BROKER_HOST", "localhost"),
        broker_port=int(os.getenv("MQTT_BROKER_PORT", "1883")),
        username=os.getenv("MQTT_USERNAME"),
        password=os.getenv("MQTT_PASSWORD"),
        tls=os.getenv("MQTT_TLS", "false").lower() == "true",
        client_id=os.getenv("MQTT_CLIENT_ID", "hydromatic-server"),
        keepalive=int(os.getenv("MQTT_KEEPALIVE", "30")),
        topic_prefix=os.getenv("MQTT_TOPIC_PREFIX", "hydromatic"),
        site_id=os.getenv("MQTT_SITE_ID", "primary"),
    )


def create_mqtt_service() -> HydromaticMQTTService:
    return HydromaticMQTTService(load_mqtt_config())


def build_lifespan(service: HydromaticMQTTService):
    @asynccontextmanager
    async def lifespan(_: Any):
        await service.start()
        try:
            yield
        finally:
            await service.stop()

    return lifespan
