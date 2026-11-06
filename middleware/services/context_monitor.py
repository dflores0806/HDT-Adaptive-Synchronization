import json
from aiomqtt import Client
from ..config import settings
from ..db import get_session
from ..models import TelemetryEvent
class ContextMonitor:
    def __init__(self):
        self.broker = settings.MQTT_BROKER
        self.port = settings.MQTT_PORT
    async def run(self):
        async with Client(self.broker, self.port) as client:
            async with client.messages() as messages:
                await client.subscribe("hdt/edge/+/telemetry")
                async for msg in messages: 
                    payload = json.loads(msg.payload.decode())
                    m = payload.get("metrics", {})
                    with get_session() as s:
                        s.add(TelemetryEvent(
                            node_id=payload.get("node_id", "unknown"),
                            latency_ms=float(m.get("latency_ms", 0.0)),
                            bandwidth_mbps=float(m.get("bandwidth_mbps", 0.0)),
                            energy_w=float(m.get("energy_w", 0.0)),
                            loss=float(m.get("loss", 0.0)),
                            drift=float(m.get("drift", 0.0)),
                        ))
                        s.commit()
