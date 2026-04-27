#!/bin/bash
# Run this script ON Mac to deploy code to RPi and run setup.
# Usage: bash scripts/deploy-rpi.sh <rpi-ip> [--agent-id rpi-sta-01] [--broker 192.168.1.100]
#
# What it does:
#   1. rsync code to RPi
#   2. SSH in and run setup-rpi.sh

set -e
export PATH="/opt/homebrew/bin:$PATH"

RPI_IP="${1:?Usage: $0 <rpi-ip> [--agent-id <id>] [--broker <ip>]}"
shift
RPI_USER="pi"
REMOTE_DIR="~/syncbench"
SETUP_ARGS="$*"

_step() { echo; echo "── $1"; }
_ok()   { echo "  ✓ $1"; }

# ── 1. rsync ─────────────────────────────────────────────────────────
_step "1. rsync → ${RPI_USER}@${RPI_IP}:${REMOTE_DIR}"
rsync -az --delete \
    --exclude='.git' \
    --exclude='.venv' \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='data/' \
    --exclude='reports/' \
    . "${RPI_USER}@${RPI_IP}:${REMOTE_DIR}/"
_ok "rsync complete"

# ── 2. Remote setup ───────────────────────────────────────────────────
_step "2. Running setup-rpi.sh on ${RPI_IP}"
ssh "${RPI_USER}@${RPI_IP}" \
    "bash ${REMOTE_DIR}/scripts/setup-rpi.sh ${SETUP_ARGS}"
