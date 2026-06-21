"""Async MQTT client wrapper (reused from the Helvar/Soundweb gateways, unchanged)."""
import asyncio
import json
from typing import Callable, Optional, Dict, Any
from aiomqtt import Client as MQTTClient
from aiologger import Logger
from .config import MQTTConfig


class MQTTWrapper:
    """Async MQTT client with reconnection and topic subscription."""

    def __init__(self, config: MQTTConfig, logger: Logger):
        self.config = config
        self.logger = logger
        self.client: Optional[MQTTClient] = None
        self._connected = False
        self._reconnect_delay = 1.0
        self._max_reconnect_delay = 60.0

    async def connect(self) -> None:
        while not self._connected:
            try:
                kwargs = {
                    "hostname": self.config.host,
                    "port": self.config.port,
                    "keepalive": self.config.keepalive,
                }
                if self.config.client_id:
                    kwargs["identifier"] = self.config.client_id
                if self.config.username is not None:
                    kwargs["username"] = self.config.username
                if self.config.password is not None:
                    kwargs["password"] = self.config.password

                self.client = MQTTClient(**kwargs)
                await self.client.__aenter__()
                self._connected = True
                self._reconnect_delay = 1.0
                await self.logger.info(
                    f"Connected to MQTT broker at {self.config.host}:{self.config.port}"
                )
            except Exception as e:
                await self.logger.error(
                    f"MQTT connection failed: {e}, retrying in {self._reconnect_delay:.1f}s"
                )
                if self.client:
                    try:
                        await self.client.__aexit__(None, None, None)
                    except Exception:
                        pass
                    self.client = None
                await asyncio.sleep(self._reconnect_delay)
                self._reconnect_delay = min(
                    self._reconnect_delay * 2, self._max_reconnect_delay
                )

    async def disconnect(self) -> None:
        if self.client and self._connected:
            try:
                await self.client.__aexit__(None, None, None)
                await self.logger.info("Disconnected from MQTT broker")
            except Exception as e:
                await self.logger.error(f"Error disconnecting from MQTT: {e}")
            self._connected = False
            self.client = None

    async def publish(
        self, topic: str, payload: Dict[str, Any], qos: int = 1, retain: bool = False
    ) -> None:
        if not self._connected or not self.client:
            await self.logger.warning(f"Cannot publish: not connected. Topic: {topic}")
            return
        try:
            await self.client.publish(
                topic, json.dumps(payload).encode(), qos=qos, retain=retain
            )
        except Exception as e:
            await self.logger.error(f"Error publishing to {topic}: {e}")
            self._connected = False
            await self.connect()

    async def subscribe_multiple(
        self, topics: Dict[str, Callable], qos: int = 1
    ) -> None:
        while True:
            await self.connect()
            for topic_pattern in topics:
                await self.client.subscribe(topic_pattern, qos=qos)

            messages = getattr(self.client, "messages", None)
            if callable(messages):
                messages = messages()
            try:
                async for message in messages:
                    topic = getattr(message.topic, "value", None) or str(message.topic)
                    for pattern, callback in topics.items():
                        if not self._topic_matches(topic, pattern):
                            continue
                        try:
                            payload = json.loads(message.payload.decode())
                            await callback(topic, payload)
                        except json.JSONDecodeError as e:
                            await self.logger.error(f"Invalid JSON on {topic}: {e}")
                        except Exception as e:
                            await self.logger.error(f"Error processing {topic}: {e}")
                        break
            except asyncio.CancelledError:
                raise
            except Exception as e:
                cause = getattr(e, "__cause__", None)
                detail = f"{e}" if cause is None else f"{e} (cause: {cause})"
                await self.logger.error(f"MQTT disconnected: {detail}")
            self._connected = False
            self.client = None

    def _topic_matches(self, topic: str, pattern: str) -> bool:
        if pattern == topic:
            return True
        parts_p, parts_t = pattern.split("/"), topic.split("/")
        if not pattern.endswith("#") and len(parts_p) != len(parts_t):
            return False
        for i, p in enumerate(parts_p):
            if p == "#":
                return True
            if i >= len(parts_t):
                return False
            if p != "+" and p != parts_t[i]:
                return False
        return len(parts_p) == len(parts_t)

    def is_connected(self) -> bool:
        return self._connected
