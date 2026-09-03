#!/usr/bin/env bash

set -euo pipefail


# ============================================================
# OpenASICManager installer
# Supported: Ubuntu / Debian
# ============================================================

if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: run this installer as root"
    exit 1
fi


SOURCE_DIR="$(
    cd "$(dirname "${BASH_SOURCE[0]}")"
    pwd
)"

INSTALL_DIR="/opt/openasicmanager"
DATA_DIR="/var/lib/openasicmanager"
CONFIG_DIR="/etc/openasicmanager"

SERVICE_USER="openasicmanager"
SERVICE_GROUP="openasicmanager"


echo "========================================"
echo " OpenASICManager Installer"
echo "========================================"


# ------------------------------------------------------------
# 1. Packages
# ------------------------------------------------------------

echo
echo "[1/8] Installing system packages..."

export DEBIAN_FRONTEND=noninteractive

apt-get update

apt-get install -y \
    python3 \
    python3-venv \
    python3-pip \
    ca-certificates \
    curl


# ------------------------------------------------------------
# 2. Service account
# ------------------------------------------------------------

echo
echo "[2/8] Creating service account..."

if ! getent group \
    "$SERVICE_GROUP" \
    >/dev/null
then

    groupadd \
        --system \
        "$SERVICE_GROUP"
fi


if ! id \
    "$SERVICE_USER" \
    >/dev/null 2>&1
then

    useradd \
        --system \
        --gid "$SERVICE_GROUP" \
        --home-dir "$DATA_DIR" \
        --shell /usr/sbin/nologin \
        "$SERVICE_USER"
fi


# ------------------------------------------------------------
# 3. Directories
# ------------------------------------------------------------

echo
echo "[3/8] Creating directories..."

install \
    -d \
    -o root \
    -g root \
    -m 0755 \
    "$INSTALL_DIR"

install \
    -d \
    -o "$SERVICE_USER" \
    -g "$SERVICE_GROUP" \
    -m 0700 \
    "$DATA_DIR"

install \
    -d \
    -o root \
    -g "$SERVICE_GROUP" \
    -m 0750 \
    "$CONFIG_DIR"


# ------------------------------------------------------------
# 4. Application
# ------------------------------------------------------------

echo
echo "[4/8] Installing application..."

rm -rf \
    "$INSTALL_DIR/app" \
    "$INSTALL_DIR/scripts"

cp -a \
    "$SOURCE_DIR/app" \
    "$INSTALL_DIR/app"

cp -a \
    "$SOURCE_DIR/scripts" \
    "$INSTALL_DIR/scripts"

cp \
    "$SOURCE_DIR/requirements.txt" \
    "$INSTALL_DIR/requirements.txt"

chown -R \
    root:root \
    "$INSTALL_DIR"

find \
    "$INSTALL_DIR/app" \
    "$INSTALL_DIR/scripts" \
    -type d \
    -exec chmod 0755 {} \;

find \
    "$INSTALL_DIR/app" \
    "$INSTALL_DIR/scripts" \
    -type f \
    -exec chmod 0644 {} \;

chmod 0755 \
    "$INSTALL_DIR/scripts/asic-firmware-detect"

if [ -f \
    "$INSTALL_DIR/scripts/asic-discover"
]; then

    chmod 0755 \
        "$INSTALL_DIR/scripts/asic-discover"
fi


# ------------------------------------------------------------
# 5. Virtual environment
# ------------------------------------------------------------

echo
echo "[5/8] Preparing Python environment..."

if [ ! -x \
    "$INSTALL_DIR/venv/bin/python"
]; then

    python3 -m venv \
        "$INSTALL_DIR/venv"
fi


"$INSTALL_DIR/venv/bin/python" \
    -m pip \
    install \
    --upgrade \
    pip


"$INSTALL_DIR/venv/bin/python" \
    -m pip \
    install \
    -r "$INSTALL_DIR/requirements.txt"


# ------------------------------------------------------------
# 6. Configuration
# ------------------------------------------------------------

echo
echo "[6/8] Preparing configuration..."

ENV_FILE="$CONFIG_DIR/openasicmanager.env"


if [ ! -f \
    "$ENV_FILE"
]; then

    cp \
        "$SOURCE_DIR/.env.example" \
        "$ENV_FILE"

    chown \
        root:"$SERVICE_GROUP" \
        "$ENV_FILE"

    chmod \
        0640 \
        "$ENV_FILE"


    echo
    echo "Created configuration:"
    echo "  $ENV_FILE"

    echo
    echo "IMPORTANT:"
    echo "Configure ASIC credentials in this file."
else

    echo "Keeping existing configuration:"
    echo "  $ENV_FILE"
fi


# ------------------------------------------------------------
# 7. systemd
# ------------------------------------------------------------

echo
echo "[7/8] Installing systemd units..."

install \
    -m 0644 \
    "$SOURCE_DIR/deploy/systemd/openasicmanager.service" \
    /etc/systemd/system/openasicmanager.service

install \
    -m 0644 \
    "$SOURCE_DIR/deploy/systemd/openasicmanager-firmware-detect.service" \
    /etc/systemd/system/openasicmanager-firmware-detect.service

install \
    -m 0644 \
    "$SOURCE_DIR/deploy/systemd/openasicmanager-firmware-detect.timer" \
    /etc/systemd/system/openasicmanager-firmware-detect.timer


systemctl daemon-reload

systemctl enable \
    openasicmanager.service

systemctl enable \
    openasicmanager-firmware-detect.timer


# ------------------------------------------------------------
# 8. Start
# ------------------------------------------------------------

echo
echo "[8/8] Starting OpenASICManager..."

systemctl restart \
    openasicmanager.service

systemctl restart \
    openasicmanager-firmware-detect.timer


sleep 3


echo
echo "===== SERVICE ====="

if systemctl is-active \
    --quiet \
    openasicmanager.service
then

    echo "OpenASICManager: ACTIVE"
else

    echo "OpenASICManager: FAILED"

    journalctl \
        -u openasicmanager.service \
        -n 50 \
        --no-pager

    exit 1
fi


echo
echo "===== HEALTH ====="

curl \
    -fsS \
    http://127.0.0.1:8088/health

echo


echo
echo "========================================"
echo " Installation completed"
echo "========================================"

echo
echo "Configuration:"
echo "  $ENV_FILE"

echo
echo "Database:"
echo "  $DATA_DIR/openasicmanager.db"

echo
echo "Local Web:"
echo "  http://127.0.0.1:8088"

echo
echo "Logs:"
echo "  journalctl -u openasicmanager -f"
