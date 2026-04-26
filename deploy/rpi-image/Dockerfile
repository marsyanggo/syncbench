# Multi-arch: works on RPi 4 (arm64) and Mac (amd64) for local testing
FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    iw \
    iproute2 \
    iputils-ping \
    chrony \
    iperf3 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install uv for fast dependency install
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Copy dependency definitions first (layer cache)
COPY pyproject.toml ./
COPY shared/ ./shared/
COPY agent/ ./agent/

# Install only agent runtime deps (no dev extras)
RUN uv pip install --system --no-dev \
    paho-mqtt \
    python-ulid

ENTRYPOINT ["python", "-m", "agent.atf_agent.main"]
CMD ["--broker", "192.168.1.10", "--port", "1883"]
