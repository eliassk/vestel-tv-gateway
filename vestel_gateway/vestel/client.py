"""Minimal Vestel Visual Solutions control client over TCP (port 1986).

Protocol (RS-232/LAN, ASCII):
  - Each command is ASCII text terminated with 0x0A (LF). Request/response, NO push.
  - Replies are single lines like:  #*volume level is 16 / #*source is HDMI1 /
    #* standby Off / #* MUTE ON   (optionally prefixed with [#NN] if a Display ID is set).
  - Power: TON <vol> (on with volume), TOF (active standby = off), GETSTANDBY -> "standby Off/On"
    (Off = on). Never use full STANDBY: it drops the network and the TV stops responding.
  - Volume: VOLUME <0-100>, GETVOLUME. Mute: "SET MUTE" toggles, "GET MUTE" reads. Source: SELECTSOURCE <n>, GETSOURCE.
"""
import asyncio
import re
import random
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from aiologger import Logger


# ---------------------------------------------------------------------------
# Pure response parsers (unit-tested, no I/O). Input is a raw reply line.
# ---------------------------------------------------------------------------
def _clean(line: str) -> str:
    """Strip optional [#NN] display-id prefix and the #*/# response markers."""
    s = line.strip()
    s = re.sub(r"^\[#\d+\]\s*", "", s)
    s = re.sub(r"^#\*?\s*", "", s)
    return s.strip()


def parse_volume(line: str) -> Optional[int]:
    m = re.search(r"volume level is\s*(\d+)", _clean(line), re.I)
    return int(m.group(1)) if m else None


def parse_mute(line: str) -> Optional[bool]:
    s = _clean(line).upper()
    if "MUTE ON" in s:
        return True
    if "MUTE OFF" in s:
        return False
    return None


def parse_power(line: str) -> Optional[bool]:
    """GETSTANDBY: 'standby Off' -> power on (True); 'standby On' -> power off (False)."""
    s = _clean(line).lower()
    if "standby off" in s:
        return True
    if "standby on" in s:
        return False
    return None


def parse_source(line: str) -> Optional[str]:
    m = re.search(r"source is\s*(.+)$", _clean(line), re.I)
    return m.group(1).strip() if m else None


class VestelClient:
    """Async Vestel TV client over TCP. One persistent connection per TV, request/response."""

    def __init__(self, tv_id: str, host: str, port: int, logger: "Logger",
                 command_timeout_ms: int = 2000, reconnect_initial_delay: float = 1.0,
                 reconnect_max_delay: float = 60.0, reconnect_jitter: float = 0.1):
        self.tv_id = tv_id
        self.host = host
        self.port = port
        self.logger = logger
        self._timeout = command_timeout_ms / 1000.0
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._connected = False
        self._lock = asyncio.Lock()
        self._reconnect_delay = reconnect_initial_delay
        self._initial_delay = reconnect_initial_delay
        self._max_delay = reconnect_max_delay
        self._jitter = reconnect_jitter

    def is_connected(self) -> bool:
        return self._connected

    async def connect(self) -> None:
        while not self._connected:
            try:
                self._reader, self._writer = await asyncio.open_connection(self.host, self.port)
                self._connected = True
                self._reconnect_delay = self._initial_delay
                await self.logger.info(f"Connected to Vestel TV {self.tv_id} at {self.host}:{self.port}")
            except Exception as e:
                await self.logger.error(
                    f"Failed to connect to {self.tv_id}: {e}, retrying in {self._reconnect_delay:.1f}s"
                )
                await self._close()
                await asyncio.sleep(self._reconnect_delay)
                self._reconnect_delay = min(
                    self._reconnect_delay * 2 + random.uniform(0, self._jitter * self._reconnect_delay),
                    self._max_delay,
                )

    async def _close(self) -> None:
        if self._writer:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception:
                pass
        self._writer = None
        self._reader = None
        self._connected = False

    async def disconnect(self) -> None:
        await self._close()
        await self.logger.info(f"Disconnected from Vestel TV {self.tv_id}")

    async def _send_recv(self, cmd: str, expect_reply: bool = True) -> Optional[str]:
        """Send a command (LF-terminated) and read one reply line. Reconnects on failure."""
        async with self._lock:
            if not self._connected:
                return None
            try:
                self._writer.write((cmd + "\n").encode("utf-8"))
                await self._writer.drain()
                if not expect_reply:
                    return ""
                line = await asyncio.wait_for(self._reader.readline(), timeout=self._timeout)
                if not line:
                    raise ConnectionError("connection closed")
                return line.decode("utf-8", errors="replace")
            except asyncio.TimeoutError:
                await self.logger.warning(f"{self.tv_id}: timeout waiting for reply to '{cmd}'")
                return None
            except Exception as e:
                await self.logger.error(f"{self.tv_id}: send/recv error on '{cmd}': {e}")
                await self._close()
                return None

    # --- queries -------------------------------------------------------------
    async def get_power(self) -> Optional[bool]:
        return parse_power(await self._send_recv("GETSTANDBY") or "")

    async def get_volume(self) -> Optional[int]:
        return parse_volume(await self._send_recv("GETVOLUME") or "")

    async def get_mute(self) -> Optional[bool]:
        return parse_mute(await self._send_recv("GET MUTE") or "")

    async def get_source(self) -> Optional[str]:
        return parse_source(await self._send_recv("GETSOURCE") or "")

    # --- commands ------------------------------------------------------------
    async def power_on(self, volume: int) -> bool:
        volume = max(0, min(100, int(volume)))
        return (await self._send_recv(f"TON {volume}")) is not None

    async def power_off(self) -> bool:
        return (await self._send_recv("TOF")) is not None

    async def set_volume(self, n: int) -> bool:
        n = max(0, min(100, int(n)))
        return (await self._send_recv(f"VOLUME {n}")) is not None

    async def set_mute_to(self, target: bool) -> bool:
        """Mute is toggle-only: read current state and toggle only if it differs."""
        cur = await self.get_mute()
        if cur is None:
            return False
        if cur != target:
            return (await self._send_recv("SET MUTE")) is not None
        return True

    async def select_source(self, code: int) -> bool:
        return (await self._send_recv(f"SELECTSOURCE {int(code)}")) is not None

    async def send_key(self, name: str) -> bool:
        """Send a remote-control key by name (e.g. 'menu', 'up', 'ok', 'vol+'). Stateless."""
        return (await self._send_recv(f"KEY {name}")) is not None

    async def send_irkey(self, hex_value: str) -> bool:
        """Send a raw IR key value, e.g. 'irkey 0x38'. Stateless."""
        return (await self._send_recv(f"irkey {hex_value}")) is not None

