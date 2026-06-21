"""Main entrypoint for Vestel TV MQTT Gateway."""
import asyncio
import io
import os
import signal
import sys
from aiologger import Logger
from aiologger.formatters.json import JsonFormatter
from vestel_gateway.config import load_config
from vestel_gateway.gateway import VestelMQTTGateway


class _SafeStream(io.TextIOBase):
    """Absorb BrokenPipeError/EPIPE on write so process does not exit when journald pipe is closed."""

    def __init__(self, stream):
        self._stream = stream

    def write(self, s):
        try:
            return self._stream.write(s)
        except (BrokenPipeError, OSError) as e:
            if getattr(e, "errno", None) != 32:  # EPIPE
                raise
            return len(s)

    def flush(self):
        try:
            return self._stream.flush()
        except (BrokenPipeError, OSError) as e:
            if getattr(e, "errno", None) != 32:
                raise

    def fileno(self):
        return self._stream.fileno()

    def __getattr__(self, name):
        return getattr(self._stream, name)


async def main():
    if hasattr(sys.stdout, "fileno") and not os.isatty(sys.stdout.fileno()):
        sys.stdout = _SafeStream(sys.stdout)
        sys.stderr = _SafeStream(sys.stderr)

    config_path = sys.argv[1] if len(sys.argv) > 1 else "config.yaml"
    try:
        config = load_config(config_path)
    except Exception as e:
        print(f"Failed to load configuration: {e}", file=sys.stderr)
        sys.exit(1)

    logger = Logger.with_default_handlers(
        name="vestel_gateway", level=config.log_level, formatter=JsonFormatter()
    )

    gateway = VestelMQTTGateway(config, logger)
    shutdown_event = asyncio.Event()

    def signal_handler(sig, frame):
        shutdown_event.set()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        await gateway.start()
        await shutdown_event.wait()
        await logger.info("Received shutdown signal")
    except Exception as e:
        await logger.error(f"Fatal error: {e}")
        raise
    finally:
        await gateway.stop()
        await logger.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
