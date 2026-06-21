# Vestel TV MQTT Gateway

Headless Python service bridging **Vestel Visual Solution displays** (RS-232/LAN control protocol over
**TCP 1986**) to an **MQTT broker** for Home Assistant. Exposes per-TV **power / volume / mute / source**
and publishes **retained** state so HA restores after a restart.

Built per the gateway conventions in [../Soundweb-gateway/CLAUDE.md](../Soundweb-gateway/CLAUDE.md);
mirrors the Helvar/Soundweb gateways (async). Because Vestel is request/response with **no push**, the
gateway **polls** each TV on an interval (like the AHU gateway).

## Architecture

- `vestel_gateway/vestel/client.py` — TCP client: persistent connection per TV, request/response with a
  lock, reconnect with backoff. Pure response parsers (`parse_volume/mute/power/source`) covered by
  `tests/test_protocol.py`.
- `vestel_gateway/gateway.py` — connect TVs, publish discovery, initial snapshot, subscribe commands,
  poll loop (publish retained), health loop. Publish-on-command + poll = retention.
- `vestel_gateway/mqtt_client.py`, `main.py` — reused from the Helvar/Soundweb gateways.

## Protocol notes (from the Vestel RS-232/LAN doc)

- TCP **1986**, ASCII commands terminated with `\n`. Replies like `#*volume level is 16`, `#*source is HDMI1`,
  `#* standby Off`, `#* MUTE ON`.
- Power: `TON <vol>` (on), `TOF` (active standby = off), `GETSTANDBY`. **Never `STANDBY`** (drops the network).
- Volume `VOLUME <0-100>` / `GETVOLUME`; Mute `SET MUTE` (toggle) / `GET MUTE`; Source `SELECTSOURCE <n>` / `GETSOURCE`.
- Source codes: AV=5, HDMI1=7, HDMI2=8, YPbPr=11, VGA=12, DVI=18, DisplayPort=19, OPS=20, Wireless=21.

## Configuration

See [config.example.yaml](config.example.yaml): MQTT broker + a list of TVs (`id`, `name`, `host`, `port`,
optional `sources` map). `poll_interval` and `default_on_volume` tune polling and the TON volume.

## Install (Ubuntu, systemd)

```bash
sudo ./install.sh                 # /opt/vestel-tv-gateway, venv, systemd unit; never overwrites config.yaml
sudo nano /opt/vestel-tv-gateway/config.yaml
sudo systemctl restart vestel-tv-gateway
journalctl -u vestel-tv-gateway -f
```
Update later: copy `vestel_gateway/` + `main.py` into `/opt/vestel-tv-gateway` and restart (leave config.yaml).

## Verify

```bash
printf 'GETVOLUME\n' | nc <TV_IP> 1986       # confirm the TV replies
python3 tests/test_protocol.py               # parser unit tests
mosquitto_sub -u ha -P '<pwd>' -t 'tv/#' -v --retained-only -W 6   # retained state per TV after start
mosquitto_pub  -u ha -P '<pwd>' -t 'tv/cmd/<id>/power' -m '{"power":"on"}'
```

## Home Assistant

Per TV: a universal `media_player` (power/volume/mute/source) wrapping helper MQTT entities, plus an
optional on-screen **remote** ([universal-remote-card](https://github.com/Nerwyn/universal-remote-card))
whose buttons publish `{"key":"<name>"}` to `tv/cmd/<id>/key` (Vestel `KEY <name>`). See
[MQTT.md](MQTT.md) for full examples and the remote key-name list.
