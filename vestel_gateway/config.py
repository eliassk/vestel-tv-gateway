"""Configuration loading and validation."""
import yaml
from pathlib import Path
from typing import Dict, List, Optional
from pydantic import BaseModel


# HelvarNet-style: default Vestel source codes (SELECTSOURCE n). Override per TV in config.
DEFAULT_SOURCES: Dict[str, int] = {
    "AV": 5, "HDMI1": 7, "HDMI2": 8, "YPbPr": 11, "VGA": 12,
    "DVI": 18, "DisplayPort": 19, "OPS": 20, "Wireless": 21,
}


class MQTTConfig(BaseModel):
    """MQTT broker configuration."""
    host: str = "localhost"
    port: int = 1883
    username: Optional[str] = None
    password: Optional[str] = None
    client_id: str = "vestel-tv-gateway"
    keepalive: int = 60
    base_topic: str = "tv"


class TVConfig(BaseModel):
    """A single Vestel display."""
    id: str
    name: Optional[str] = None
    host: str
    port: int = 1986
    sources: Dict[str, int] = {}  # label -> SELECTSOURCE code; empty = use DEFAULT_SOURCES

    def source_map(self) -> Dict[str, int]:
        return self.sources or DEFAULT_SOURCES


class VestelConfig(BaseModel):
    tvs: List[TVConfig]
    poll_interval: float = 15.0
    default_on_volume: int = 20      # volume used for TON when turning a TV on
    command_timeout_ms: int = 2000
    reconnect_initial_delay: float = 1.0
    reconnect_max_delay: float = 60.0
    reconnect_jitter: float = 0.1


class GatewayConfig(BaseModel):
    """Main gateway configuration."""
    mqtt: MQTTConfig
    vestel: VestelConfig
    log_level: str = "INFO"


def load_config(config_path: str = "config.yaml") -> GatewayConfig:
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Configuration file not found: {config_path}")
    with open(path, "r") as f:
        data = yaml.safe_load(f)
    return GatewayConfig(**data)
