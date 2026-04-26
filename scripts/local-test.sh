#!/bin/bash
# Local test script — runs all tests + smoke test on Mac (no RPi needed)
# Usage: bash scripts/local-test.sh

set -e
export PATH="/opt/homebrew/bin:$PATH"
cd "$(dirname "$0")/.."

PASS=0
FAIL=0

_ok()   { echo "  ✓ $1"; PASS=$((PASS+1)); }
_fail() { echo "  ✗ $1"; FAIL=$((FAIL+1)); }
_step() { echo; echo "── $1"; }

# ── Cleanup on exit ──────────────────────────────────────────────────
cleanup() {
    [ -n "$AGENT_PID" ] && kill "$AGENT_PID" 2>/dev/null || true
    pkill -f "iperf3 -s" 2>/dev/null || true
    pkill -f "iperf3 --server" 2>/dev/null || true
}
trap cleanup EXIT

# ── 1. Infrastructure check ───────────────────────────────────────────
_step "1. Infrastructure"
if docker compose ps 2>/dev/null | grep -q "Up"; then
    _ok "docker-compose services running"
else
    echo "  Starting docker-compose..."
    docker compose up -d
    sleep 3
    _ok "docker-compose started"
fi

# ── 2. Unit tests ────────────────────────────────────────────────────
_step "2. Unit tests (no iperf3)"
if uv run pytest controller/tests/test_mqtt_bus.py \
                   controller/tests/test_scenario_loader.py \
                   -q 2>&1 | tail -1 | grep -q "passed"; then
    _ok "MQTT bus + scenario loader tests passed"
else
    _fail "Unit tests failed"
    uv run pytest controller/tests/test_mqtt_bus.py \
                   controller/tests/test_scenario_loader.py -v
fi

# ── 3. iperf3 tests ──────────────────────────────────────────────────
_step "3. iperf3 runner tests"
iperf3 -s -D --logfile /tmp/atf-iperf3.log 2>/dev/null
sleep 0.5
if uv run pytest agent/tests/test_iperf3.py -q 2>&1 | tail -1 | grep -q "passed"; then
    _ok "iperf3 runner tests passed"
else
    _fail "iperf3 runner tests failed"
fi

# ── 4. End-to-end smoke test ─────────────────────────────────────────
_step "4. End-to-end smoke test"

# Start agent
uv run atf-agent --broker localhost --agent-id rpi-sta-01 \
    > /tmp/atf-agent.log 2>&1 &
AGENT_PID=$!
sleep 2

# Check agent is running
if ! kill -0 "$AGENT_PID" 2>/dev/null; then
    _fail "Agent failed to start (check /tmp/atf-agent.log)"
else
    _ok "Agent started (pid $AGENT_PID)"

    if uv run atf-run scenarios/00_smoke_test_local.yaml -q; then
        _ok "atf-run smoke test PASSED"
    else
        _fail "atf-run smoke test FAILED"
    fi
fi

# ── Summary ──────────────────────────────────────────────────────────
echo
echo "══════════════════════════════"
echo "  PASS: $PASS   FAIL: $FAIL"
echo "══════════════════════════════"
[ "$FAIL" -eq 0 ]
