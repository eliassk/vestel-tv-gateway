# MQTT API Reference

Base topic: `mqtt.base_topic` (default `tv`). Payloads JSON, QoS 1. State and discovery are retained.

## Discovery (gateway → clients, retained)

`tv/discovery` — list of TVs and their source labels:
```json
{ "tvs": [ { "id": "sala-glowna", "name": "Sala Główna", "sources": ["HDMI1","HDMI2","OPS"] } ] }
```

## Commands (client → gateway)

| Topic | Payload | Description |
|-------|---------|-------------|
| `tv/cmd/<id>/power` | `{"power": "on"\|"off"}` | Power on (TON) / active-standby (TOF). |
| `tv/cmd/<id>/volume` | `{"volume": 0-100}` | Set volume. |
| `tv/cmd/<id>/mute` | `{"mute": true\|false}` | Mute / unmute (toggle reconciled to target). |
| `tv/cmd/<id>/source` | `{"source": "<label>"}` | Select input (label from discovery). |
| `tv/cmd/<id>/key` | `{"key": "<name>"}` or `{"irkey": "0x38"}` | Send a remote-control key (stateless, no state topic). |

**Remote key names** (Vestel `KEY <name>`): `standby`, `up` `down` `left` `right` `ok`, `menu`,
`quick_menu`, `exit` (back) / `exit2`, `vol+` `vol-`, `mute`, `prog+` `prog-`, `aux` (source),
`0`–`9`, `red` `green` `yellow` `blue`, `play` `stop` `pause` `fforward` `rewind`,
`info`, `picture`, `preset`, `audio`, `wide` (aspect), `browser`, `media_browser`, `wireless`,
`internet_settings`, `tiling`, `signage`, `star_key`.

```bash
mosquitto_pub -t 'tv/cmd/sala-glowna/power'  -m '{"power":"on"}'
mosquitto_pub -t 'tv/cmd/sala-glowna/volume' -m '{"volume":25}'
mosquitto_pub -t 'tv/cmd/sala-glowna/mute'   -m '{"mute":true}'
mosquitto_pub -t 'tv/cmd/sala-glowna/source' -m '{"source":"HDMI1"}'
```

## State (gateway → clients, retained)

Published on command and on each poll (`poll_interval`).

| Topic | Payload |
|-------|---------|
| `tv/state/<id>/power` | `{"tv","power":"on"\|"off","ts"}` |
| `tv/state/<id>/volume` | `{"tv","volume":0-100,"ts"}` |
| `tv/state/<id>/mute` | `{"tv","mute":bool,"ts"}` |
| `tv/state/<id>/source` | `{"tv","source":"<label>","ts"}` |

## Health (gateway → clients, not retained)

`tv/health/<id>` → `{"tv","online":bool,"ts"}` every 30 s (TCP connection status).

## Home Assistant — universal media_player (per TV)

Helper MQTT entities (under your existing `mqtt:`):
```yaml
mqtt:
  sensor:
    - { name: "Sala Glowna TV volume level", state_topic: "tv/state/sala-glowna/volume", value_template: "{{ (value_json.volume | float(0)) / 100 }}" }
    - { name: "Sala Glowna TV source",        state_topic: "tv/state/sala-glowna/source", value_template: "{{ value_json.source }}" }
  binary_sensor:
    - { name: "Sala Glowna TV muted", state_topic: "tv/state/sala-glowna/mute",  value_template: "{{ 'ON' if value_json.mute else 'OFF' }}", payload_on: "ON", payload_off: "OFF" }
    - { name: "Sala Glowna TV power", state_topic: "tv/state/sala-glowna/power", value_template: "{{ 'ON' if value_json.power == 'on' else 'OFF' }}", payload_on: "ON", payload_off: "OFF" }
```
```yaml
media_player:
  - platform: universal
    name: "Sala Główna TV"
    unique_id: vestel_sala_glowna
    device_class: tv
    state_template: "{{ 'on' if is_state('binary_sensor.sala_glowna_tv_power','on') else 'off' }}"
    attributes:
      volume_level: sensor.sala_glowna_tv_volume_level
      is_volume_muted: binary_sensor.sala_glowna_tv_muted
      source: sensor.sala_glowna_tv_source
    commands:
      turn_on:  { action: mqtt.publish, data: { topic: "tv/cmd/sala-glowna/power", payload: '{"power":"on"}' } }
      turn_off: { action: mqtt.publish, data: { topic: "tv/cmd/sala-glowna/power", payload: '{"power":"off"}' } }
      volume_set: { action: mqtt.publish, data: { topic: "tv/cmd/sala-glowna/volume", payload: '{"volume": {{ (volume_level * 100) | round | int }}}' } }
      volume_mute: { action: mqtt.publish, data: { topic: "tv/cmd/sala-glowna/mute", payload: '{"mute": {{ is_volume_muted | lower }}}' } }
      select_source: { action: mqtt.publish, data: { topic: "tv/cmd/sala-glowna/source", payload: '{"source": "{{ source }}"}' } }
```
Note: universal `media_player` is a legacy YAML platform — it needs a **full HA restart** to load (not a quick reload). `source_list` can be added via a template if you want a fixed dropdown.

## Remote control (Universal Remote Card)

The gateway has **no HA `remote` entity** — remote keys are stateless `mqtt.publish` commands to
`tv/cmd/<id>/key`. Use [universal-remote-card](https://github.com/Nerwyn/universal-remote-card) (HACS)
and point each button's action at `mqtt.publish`. Core pattern for one button:

```yaml
type: custom:universal-remote-card
rows:
  - - power
  - - channel_up
  - - up
  - - left
    - center
    - right
  - - down
  - - back
    - home
    - volume_buttons
custom_actions:
  - name: power
    icon: mdi:power
    tap_action: &key { action: perform-action, perform_action: mqtt.publish, data: { topic: "tv/cmd/sala-glowna/key", payload: '{"key":"standby"}' } }
  - name: up
    icon: mdi:chevron-up
    tap_action: { action: perform-action, perform_action: mqtt.publish, data: { topic: "tv/cmd/sala-glowna/key", payload: '{"key":"up"}' } }
  - name: down
    icon: mdi:chevron-down
    tap_action: { action: perform-action, perform_action: mqtt.publish, data: { topic: "tv/cmd/sala-glowna/key", payload: '{"key":"down"}' } }
  - name: left
    icon: mdi:chevron-left
    tap_action: { action: perform-action, perform_action: mqtt.publish, data: { topic: "tv/cmd/sala-glowna/key", payload: '{"key":"left"}' } }
  - name: right
    icon: mdi:chevron-right
    tap_action: { action: perform-action, perform_action: mqtt.publish, data: { topic: "tv/cmd/sala-glowna/key", payload: '{"key":"right"}' } }
  - name: center
    icon: mdi:circle-medium
    tap_action: { action: perform-action, perform_action: mqtt.publish, data: { topic: "tv/cmd/sala-glowna/key", payload: '{"key":"ok"}' } }
  - name: back
    icon: mdi:arrow-left
    tap_action: { action: perform-action, perform_action: mqtt.publish, data: { topic: "tv/cmd/sala-glowna/key", payload: '{"key":"exit"}' } }
  - name: home
    icon: mdi:home
    tap_action: { action: perform-action, perform_action: mqtt.publish, data: { topic: "tv/cmd/sala-glowna/key", payload: '{"key":"menu"}' } }
  - name: channel_up
    icon: mdi:menu
    tap_action: { action: perform-action, perform_action: mqtt.publish, data: { topic: "tv/cmd/sala-glowna/key", payload: '{"key":"quick_menu"}' } }
```

Each button just publishes `{"key":"<name>"}` to the TV's key topic (replace `sala-glowna`). Volume
buttons can target `vol+`/`vol-`/`mute` keys, or use the media_player volume instead. The card's exact
option names evolve between versions — check its README and adjust `rows`/`custom_actions` to match the
installed version; the only fixed part is the `mqtt.publish` action payload above.
