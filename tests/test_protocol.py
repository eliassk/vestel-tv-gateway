"""Offline unit tests for the Vestel response parsers."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vestel_gateway.vestel.client import (  # noqa: E402
    _clean, parse_volume, parse_mute, parse_power, parse_source,
)


def test_clean_strips_markers_and_id():
    assert _clean("#*volume level is 16") == "volume level is 16"
    assert _clean("#* standby Off") == "standby Off"
    assert _clean("[#02] #*source is HDMI1") == "source is HDMI1"


def test_parse_volume():
    assert parse_volume("#*volume level is 16") == 16
    assert parse_volume("#*set volume to 15") is None  # not a "volume level is" reply
    assert parse_volume("garbage") is None


def test_parse_mute():
    assert parse_mute("#* MUTE ON") is True
    assert parse_mute("#* MUTE OFF") is False
    assert parse_mute("#*volume level is 5") is None


def test_parse_power():
    assert parse_power("#* standby Off") is True       # standby off => powered on
    assert parse_power("#* standby On") is False        # standby on => off
    assert parse_power("whatever") is None


def test_parse_source():
    assert parse_source("#*source is HDMI1") == "HDMI1"
    assert parse_source("#*source is Display Port") == "Display Port"
    assert parse_source("nope") is None


if __name__ == "__main__":
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except Exception:
            failed += 1
            print(f"FAIL {fn.__name__}")
            traceback.print_exc()
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
