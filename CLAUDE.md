# CLAUDE.md — KOACH MQTT Gateway Constitution

This document defines **how MQTT gateways are built, structured, deployed and operated** at KOACH.
It is the shared "constitution" distilled from the existing gateways:

- **Helvar gateway** — lighting, HelvarNet ASCII over TCP, **async** (aiomqtt/asyncio).
- **AHU gateway** — VTS air handling units, REST getvar/setvar, **sync** (paho-mqtt) polling.
- **Soundweb gateway** — BSS Soundweb London audio, London DI over TCP, **async**, push on subscribe.

This repo (`vestel-tv-gateway`) is a gateway for **Vestel Visual Solution displays** (RS-232/LAN control
over TCP 1986). It is a **separate repo**; this file is duplicated here on purpose so the critical
conventions travel with the code. Follow this document unless a deviation is justified and written here.

---

## 1. Purpose & golden rule

A gateway **bridges one building subsystem to the MQTT bus** so Home Assistant (and other clients) can
read state and send commands. The broker and HA live on the server (`mqttbusvm`); each gateway is a
small headless service.

**One service per subsystem.** Lighting, ventilation, audio, displays are separate gateways with separate
repos, configs, systemd units and MQTT base topics. Never merge two subsystems into one process:
different protocols, independent restarts, smaller blast radius, simpler config.

---

## 2. THE retention rule (most important operational invariant)

Home Assistant must restore correct state after **its own restart**. That only works if a **retained**
message already exists on the broker for each entity's `state` topic. Therefore every gateway MUST:

1. **Publish state `retain=True`** on all `state/` and `discovery/` topics.
2. **Actually produce that state**, from BOTH:
   - **On command**: immediately after a command is applied, publish the new state (optimistic) —
     the gateway knows what it just set. This guarantees a retained value for anything HA controls.
   - **At startup**: take an **initial snapshot** of every configured entity (query the device, or
     subscribe to push), and publish it retained — covers entities changed outside HA / never yet touched.
3. **Never rely solely on the device pushing changes.** Some systems never push (HelvarNet; **Vestel is
   request/response — no push, so it must be polled**); some push only on change, never the current value
   at connect. Either way the startup snapshot is mandatory.

Background: HA showed `unknown` after restart because the Helvar gateway only published reactively and the
routers never pushed group levels — nothing was retained. Fixed with publish-on-command + startup snapshot.
**Do not regress this.** Same applies to Modbus relays (`verify:`), audio, and TVs.

Also: retained survives a *gateway* restart, but a *broker* restart wipes it unless Mosquitto has
`persistence true`. The startup snapshot/poll rebuilds it regardless — keep it.

---

## 3. MQTT topic conventions

Each gateway has its own `base_topic` (Helvar `lighting`, AHU `ahu`, Soundweb `audio`, Vestel `tv`).
Payloads are **JSON**, **QoS 1**. Standard topic shapes:

| Topic | Direction | Retained | Purpose |
|---|---|---|---|
| `<base>/discovery` and `<base>/discovery/<id>/...` | gateway → clients | yes | what exists (devices, valid ids) |
| `<base>/state/<id>/<sub>` | gateway → clients | yes | last known state (power, volume, source…) |
| `<base>/cmd/<id>/<sub>` | client → gateway | no | commands (power/volume/mute/source/key…) |
| `<base>/health/<device>` | gateway → clients | no | online/offline |

Include a `ts` (ISO-8601 UTC) in state payloads. Dedup before publishing (skip if unchanged) to avoid
churn and to break the publish→self-subscribe feedback loop where a gateway also subscribes to `state`.
**Stateless actions** (e.g. remote key presses) are fire-and-forget commands with NO `state` topic.

---

## 4. Architecture: async vs sync

Pick by how the device communicates:

- **Async (aiomqtt + asyncio)** — for **persistent TCP connections** with reader loops and/or device push
  (Helvar, Soundweb). A device client owns the socket; the gateway orchestrates MQTT.
- **Sync (paho-mqtt) + poll loop** — for request/response REST/Modbus polled on an interval (AHU).

**Vestel uses async with a poll loop**: persistent TCP per TV, but request/response (no push), so state
comes from a periodic poll plus optimistic publish-on-command. Connections may be single-session and
dropped when idle → reconnect with backoff.

### Device client (`<sys>/client.py`)
- Connect with **reconnect + exponential backoff + jitter**.
- **Pure encode/decode helpers** (framing, checksums, response parsers, value scaling) as standalone
  functions **covered by offline unit tests** (e.g. Helvar `_decode_lsig_value`, Vestel `parse_volume`).
- For request/response protocols, guard the socket with an `asyncio.Lock` (`_send_recv`).
- Methods do one protocol action each (`set_x`, `get_x`) and return success/bool. One device's failure
  must never crash the process; log and reconnect.

### Gateway (`gateway.py`)
- `start()`: connect device(s) → publish `discovery` (retained) → **initial snapshot/poll** (publish
  retained, seed in-memory last-state) → subscribe MQTT `cmd` → poll loop (if no push) → health loop.
- `_publish_state(...)`: single helper that dedups against in-memory last-state and publishes retained.
- Command handler → call device client; on success publish state optimistically.
- `stop()`: disconnect MQTT first (unblocks subscribe loop), cancel tasks, disconnect devices.

---

## 5. Configuration

- `config.yaml` (gitignored; ship `config.example.yaml`). Load + validate with **pydantic** models.
- Always include: `mqtt` (host, port, username, password, client_id, keepalive, base_topic) and the
  subsystem section (devices and any poll/timeout/reconnect tuning). Sensible defaults; document in README.

---

## 6. Project layout (mirror the other gateways)

```
vestel-tv-gateway/
  CLAUDE.md                  # this file
  vestel_gateway/
    __init__.py
    config.py                # pydantic config models + load_config
    mqtt_client.py           # COPY from Helvar/Soundweb (async wrapper)
    gateway.py               # orchestration (start/stop, _publish_state, cmd handlers, poll, health)
    vestel/
      __init__.py
      client.py              # Vestel TCP client + pure response parsers (unit-tested)
  main.py                    # entrypoint (load config, logger, signals; incl. _SafeStream)
  config.example.yaml
  requirements.txt           # aiomqtt, pydantic, pydantic-settings, pyyaml, aiologger
  install.sh                 # /opt install + venv + systemd; never overwrites config.yaml
  vestel-tv-gateway.service  # systemd unit
  README.md  MQTT.md  .gitignore
  tests/test_protocol.py
```

`mqtt_client.py` and `main.py` (with the `_SafeStream` BrokenPipe guard) are **reused almost verbatim**
across gateways. Don't rewrite what already works.

---

## 7. Logging

- Async gateways: **aiologger** with `JsonFormatter`. Output to stdout/stderr → journald.
- `main.py` wraps stdout/stderr in `_SafeStream` so a closed journald pipe (EPIPE) never kills the process.

---

## 8. Deployment & operations (Ubuntu, systemd, venv — no Docker)

- Install dir `/opt/<name>-gateway`, venv at `/opt/<name>-gateway/venv`.
- systemd unit `<name>-gateway.service`: `Type=simple`, dedicated `User`/`Group`, `ExecStart` = venv python
  + `main.py` + `config.yaml`, `Restart=always`, `RestartSec=10`, journal logging.
- Server SSH key for GitHub is on **root** → use `sudo git clone/pull`. Stuck `apt.systemd.daily` can hold
  the apt lock; `install.sh` skips apt when python3-venv/pip already exist.

**Update procedure (safe):** copy only code (`<pkg>/`, `main.py`) into `/opt/<name>-gateway` and
`systemctl restart` — **NEVER overwrite the live `config.yaml`**.

**Verify after deploy:**
```bash
sudo systemctl restart <name>-gateway
journalctl -u <name>-gateway -n 50 --no-pager
mosquitto_sub -h localhost -u ha -P '<pwd>' -t '<base>/#' -v --retained-only -W 5
```

---

## 9. Home Assistant mapping

HA entities are **manually defined MQTT entities** (the gateway's `discovery` topic is informational only;
no HA-format auto-discovery is published). Conventions by domain:

- Lighting group → `light` (schema: template). AHU → `climate`. Relay channel → `switch` (with `verify:`).
- Audio zone / **TV** → **universal `media_player`** wrapping helper MQTT entities (`sensor` for
  volume-level 0–1 and source, `binary_sensor` for mute/power), with `commands` calling `mqtt.publish`.
  Universal media_player is a legacy YAML platform → needs a **full HA restart** to load.
- **Remote control**: expose a stateless `<base>/cmd/<id>/key` command (gateway sends the device key, e.g.
  Vestel `KEY <name>`). Drive it from a Lovelace remote (e.g. `universal-remote-card`) whose buttons call
  `mqtt.publish` to the key topic. No HA `remote` entity / no `state` topic for keys.

State topics must be retained (see §2) so entities restore after HA restart.

---

## 10. Working agreements

- Match the surrounding style of the existing gateways; reuse their patterns and files.
- Any protocol value / command must be **verified against the vendor doc or a live device test** before
  being hard-coded — never guess silently. Log anything you intentionally drop.
- Keep changes minimal and focused; extend the established pattern rather than invent new ones.
- Update `README.md` / `MQTT.md` when topics or config change.
```
