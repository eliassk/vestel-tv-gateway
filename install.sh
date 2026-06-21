#!/bin/bash
# Install / update the Vestel TV MQTT Gateway on Debian/Ubuntu.
# Safe to re-run: it NEVER overwrites an existing config.yaml (only copies the example if missing).
set -e

INSTALL_DIR="/opt/vestel-tv-gateway"
SERVICE_USER="vestel"
SERVICE_FILE="vestel-tv-gateway.service"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

if [ "$(id -u)" -ne 0 ]; then echo "Please run as root (sudo)"; exit 1; fi

if python3 -m venv --help >/dev/null 2>&1 && command -v pip3 >/dev/null 2>&1; then
    echo "python3-venv/pip already present — skipping apt."
else
    echo "Installing system dependencies via apt..."
    apt-get update && apt-get install -y python3-pip python3-venv || {
        echo "apt failed (locked/offline). Install python3-venv and python3-pip manually, then re-run."
        exit 1
    }
fi

id "$SERVICE_USER" >/dev/null 2>&1 || useradd -r -s /bin/false -d "$INSTALL_DIR" "$SERVICE_USER"

mkdir -p "$INSTALL_DIR"

echo "Copying application code (config.yaml is left untouched)..."
cp -r "$SCRIPT_DIR/vestel_gateway" "$INSTALL_DIR/"
cp "$SCRIPT_DIR/main.py" "$SCRIPT_DIR/requirements.txt" "$INSTALL_DIR/"
if [ ! -f "$INSTALL_DIR/config.yaml" ]; then
    cp "$SCRIPT_DIR/config.example.yaml" "$INSTALL_DIR/config.yaml"
    echo "Created $INSTALL_DIR/config.yaml from example — edit it before starting."
fi

if [ ! -d "$INSTALL_DIR/venv" ]; then
    python3 -m venv "$INSTALL_DIR/venv"
fi
"$INSTALL_DIR/venv/bin/pip" install --upgrade pip
"$INSTALL_DIR/venv/bin/pip" install -r "$INSTALL_DIR/requirements.txt"

chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR"

cp "$SCRIPT_DIR/$SERVICE_FILE" /etc/systemd/system/
systemctl daemon-reload
systemctl enable "$SERVICE_FILE"

echo "Done. Edit $INSTALL_DIR/config.yaml then: sudo systemctl restart vestel-tv-gateway"
