import json
import logging
import time
from typing import Callable

import paho.mqtt.client as mqtt
from ulid import ULID

logger = logging.getLogger(__name__)


class MQTTBus:
    """Shared MQTT client for controller and agent.

    Every published payload gets an envelope injected automatically:
      {"v": 1, "ts": <epoch_ms>, "msg_id": "<ULID>", ...original fields}
    """

    def __init__(self) -> None:
        self._client: mqtt.Client | None = None
        self._subscriptions: dict[str, Callable] = {}

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    def connect(
        self,
        broker: str,
        port: int,
        client_id: str,
        lwt_topic: str | None = None,
        lwt_payload: dict | None = None,
        keepalive: int = 60,
    ) -> None:
        self._client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=client_id,
        )
        self._client.on_connect = self._on_connect
        self._client.on_message = self._on_message
        self._client.on_disconnect = self._on_disconnect

        if lwt_topic and lwt_payload:
            self._client.will_set(
                lwt_topic,
                payload=json.dumps(self._with_envelope(lwt_payload)),
                qos=1,
                retain=True,
            )

        self._client.connect(broker, port, keepalive=keepalive)
        logger.info("Connecting to %s:%d as %s", broker, port, client_id)

    def disconnect(self) -> None:
        if self._client:
            self._client.disconnect()

    # ------------------------------------------------------------------
    # Publish / Subscribe
    # ------------------------------------------------------------------

    def publish(
        self,
        topic: str,
        payload: dict,
        qos: int = 1,
        retain: bool = False,
    ) -> None:
        assert self._client, "call connect() first"
        data = json.dumps(self._with_envelope(payload))
        self._client.publish(topic, data, qos=qos, retain=retain)

    def subscribe(self, topic: str, callback: Callable, qos: int = 1) -> None:
        assert self._client, "call connect() first"
        self._subscriptions[topic] = callback
        self._client.subscribe(topic, qos=qos)

    # ------------------------------------------------------------------
    # Event loop
    # ------------------------------------------------------------------

    def loop_start(self) -> None:
        assert self._client
        self._client.loop_start()

    def loop_stop(self) -> None:
        assert self._client
        self._client.loop_stop()

    def loop_forever(self) -> None:
        assert self._client
        self._client.loop_forever()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _with_envelope(self, payload: dict) -> dict:
        return {
            "v": 1,
            "ts": int(time.time() * 1000),
            "msg_id": str(ULID()),
            **payload,
        }

    def _on_connect(self, client, userdata, connect_flags, reason_code, properties):
        if reason_code.is_failure:
            logger.error("Connection failed: %s", reason_code)
            return
        logger.info("Connected (rc=%s)", reason_code)
        for topic in self._subscriptions:
            client.subscribe(topic)

    def _on_message(self, client, userdata, message):
        topic = message.topic
        try:
            payload = json.loads(message.payload.decode())
        except (json.JSONDecodeError, UnicodeDecodeError):
            logger.warning("Invalid payload on %s", topic)
            return
        for pattern, callback in self._subscriptions.items():
            if mqtt.topic_matches_sub(pattern, topic):
                callback(topic, payload)
                return

    def _on_disconnect(self, client, userdata, disconnect_flags, reason_code, properties):
        if reason_code.value != 0:
            logger.warning("Unexpected disconnect: %s", reason_code)
