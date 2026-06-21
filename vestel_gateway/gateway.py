"""Gateway: bridge Vestel TVs to MQTT. Poll state, publish retained, apply commands."""
import asyncio
from typing import Dict, Optional, Tuple
from datetime import datetime
from aiologger import Logger
from .config import GatewayConfig, TVConfig, DEFAULT_SOURCES
from .mqtt_client import MQTTWrapper
from .vestel.client import VestelClient


class VestelMQTTGateway:
    """Maps configured TVs to MQTT media_player-style topics. Polls (no push); publishes retained.

    Retention rule (see ../Soundweb-gateway/CLAUDE.md): publish retained on command AND poll/snapshot
    so HA restores state after its own restart.
    """

    def __init__(self, config: GatewayConfig, logger: Logger):
        self.config = config
        self.logger = logger
        self.mqtt = MQTTWrapper(config.mqtt, logger)
        self.tvs: Dict[str, TVConfig] = {t.id: t for t in config.vestel.tvs}
        self.clients: Dict[str, VestelClient] = {}
        self._last_state: Dict[Tuple[str, str], object] = {}
        self._last_volume: Dict[str, int] = {}  # for TON when powering on
        self._running = False
        self._tasks: list[asyncio.Task] = []

    async def start(self) -> None:
        await self.logger.info("Starting Vestel TV MQTT Gateway")
        self._running = True
        await self.mqtt.connect()

        v = self.config.vestel
        for tv in v.tvs:
            client = VestelClient(
                tv.id, tv.host, tv.port, self.logger,
                v.command_timeout_ms, v.reconnect_initial_delay, v.reconnect_max_delay, v.reconnect_jitter,
            )
            self.clients[tv.id] = client
            self._tasks.append(asyncio.create_task(client.connect()))

        await asyncio.sleep(2)
        await self._publish_discovery()
        await self._poll_once()  # initial snapshot -> retained state before subscribing
        await self._subscribe_commands()
        self._tasks.append(asyncio.create_task(self._poll_loop()))
        self._tasks.append(asyncio.create_task(self._health_loop()))
        await self.logger.info("Gateway started")

    async def stop(self) -> None:
        await self.logger.info("Stopping gateway")
        self._running = False
        await self.mqtt.disconnect()
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        for c in self.clients.values():
            await c.disconnect()
        await self.logger.info("Gateway stopped")

    async def _publish_discovery(self) -> None:
        base = self.config.mqtt.base_topic
        await self.mqtt.publish(
            f"{base}/discovery",
            {"tvs": [{"id": t.id, "name": t.name or t.id, "sources": list(t.source_map().keys())}
                     for t in self.config.vestel.tvs]},
            retain=True,
        )

    # --- state ---------------------------------------------------------------
    async def _publish_state(self, tv_id: str, kind: str, value) -> None:
        if value is None:
            return
        key = (tv_id, kind)
        if self._last_state.get(key) == value:
            return
        self._last_state[key] = value
        base = self.config.mqtt.base_topic
        payload = {"tv": tv_id, kind: value, "ts": datetime.utcnow().isoformat() + "Z"}
        await self.mqtt.publish(f"{base}/state/{tv_id}/{kind}", payload, retain=True)

    async def _poll_tv(self, tv_id: str) -> None:
        client = self.clients.get(tv_id)
        if not client or not client.is_connected():
            return
        power = await client.get_power()
        await self._publish_state(tv_id, "power", "on" if power else "off" if power is not None else None)
        vol = await client.get_volume()
        if vol is not None:
            self._last_volume[tv_id] = vol
        await self._publish_state(tv_id, "volume", vol)
        await self._publish_state(tv_id, "mute", await client.get_mute())
        await self._publish_state(tv_id, "source", self._source_label(self.tvs[tv_id], await client.get_source()))

    def _source_label(self, tv: TVConfig, token: Optional[str]) -> Optional[str]:
        """Map the device-reported source token (e.g. 'HDMI1') to the configured label (e.g. 'Stream')."""
        if token is None:
            return None
        code = DEFAULT_SOURCES.get(token)
        if code is not None:
            for label, c in tv.source_map().items():
                if c == code:
                    return label
        return token

    async def _poll_once(self) -> None:
        for tv_id in self.tvs:
            await self._poll_tv(tv_id)
            await asyncio.sleep(0.05)

    async def _poll_loop(self) -> None:
        interval = self.config.vestel.poll_interval
        while self._running:
            try:
                await asyncio.sleep(interval)
                await self._poll_once()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                await self.logger.error(f"Poll loop: {e}")
                await asyncio.sleep(interval)

    # --- commands ------------------------------------------------------------
    async def _subscribe_commands(self) -> None:
        base = self.config.mqtt.base_topic
        self._tasks.append(
            asyncio.create_task(self.mqtt.subscribe_multiple({f"{base}/cmd/+/+": self._on_cmd}))
        )

    async def _on_cmd(self, topic: str, payload: dict) -> None:
        parts = topic.split("/")
        try:
            idx = parts.index("cmd")
            tv_id = parts[idx + 1]
            action = parts[idx + 2]
        except (ValueError, IndexError):
            return
        tv = self.tvs.get(tv_id)
        client = self.clients.get(tv_id) if tv else None
        if not tv or not client or not client.is_connected():
            return

        if action == "power":
            val = payload.get("power", payload.get("value"))
            on = val is True or str(val).lower() in ("on", "true", "1")
            if on:
                vol = self._last_volume.get(tv_id, self.config.vestel.default_on_volume)
                if await client.power_on(vol):
                    await self._publish_state(tv_id, "power", "on")
            else:
                if await client.power_off():
                    await self._publish_state(tv_id, "power", "off")

        elif action == "volume":
            vol = payload.get("volume", payload.get("value"))
            if isinstance(vol, (int, float)):
                vol = max(0, min(100, int(vol)))
                if await client.set_volume(vol):
                    self._last_volume[tv_id] = vol
                    await self._publish_state(tv_id, "volume", vol)

        elif action == "mute":
            mute = payload.get("mute", payload.get("value"))
            if mute is not None:
                target = mute is True or str(mute).lower() in ("on", "true", "1")
                if await client.set_mute_to(target):
                    await self._publish_state(tv_id, "mute", target)

        elif action == "source":
            label = payload.get("source", payload.get("value"))
            code = tv.source_map().get(label)
            if code is not None and await client.select_source(code):
                await self._publish_state(tv_id, "source", label)

        elif action == "key":
            # Stateless remote-control key (e.g. {"key": "menu"} or {"irkey": "0x38"}). No state topic.
            irkey = payload.get("irkey")
            key = payload.get("key", payload.get("value"))
            if irkey is not None:
                await client.send_irkey(str(irkey))
            elif key is not None:
                await client.send_key(str(key))

    async def _health_loop(self) -> None:
        while self._running:
            try:
                base = self.config.mqtt.base_topic
                for tv_id, client in self.clients.items():
                    await self.mqtt.publish(
                        f"{base}/health/{tv_id}",
                        {"tv": tv_id, "online": client.is_connected(),
                         "ts": datetime.utcnow().isoformat() + "Z"},
                    )
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                await self.logger.error(f"Health loop: {e}")
                await asyncio.sleep(30)
