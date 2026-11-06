"""MQTT policy publisher; one session per decision cycle, not per node.

MQTT QoS=1 acknowledges delivery to the broker, NOT policy application by an
edge agent; end-to-end ACK instrumentation remains an experimental limitation.
"""
import json
from aiomqtt import Client
from ..config import settings

class PolicyPublisher:
    def __init__(self):
        self.broker = settings.MQTT_BROKER
        self.port = settings.MQTT_PORT

    async def send_policy(self, node_id: str, policy: dict):
        errors = await self.send_many({node_id: policy})
        if errors:
            raise RuntimeError(f'Policy publish failed for {node_id}: {errors[node_id]}')

    async def send_many(self, policies: dict[str, dict]) -> dict[str, str]:
        """Return per-node errors, avoiding a connection per agent.

        If broker connection fails, every unsent command is marked as failed.
        Do not interpret a successful broker publish as an agent ACK.
        """
        failed = {}
        if not policies:
            return failed
        try:
            async with Client(self.broker, self.port) as client:
                for node_id, policy in policies.items():
                    try:
                        payload = json.dumps(policy, separators=(',', ':')).encode('utf8')
                        await client.publish(f'hdt/edge/{node_id}/policy', payload, qos=1)
                    except Exception as exc:
                        failed[node_id] = repr(exc)
        except Exception as exc:
            failed.update({node_id: repr(exc) for node_id in policies if node_id not in failed})
        return failed
