# Development Setup Guide

This guide covers everything needed to set up the ATF Validator development environment on macOS (Apple Silicon).

---

## Prerequisites

### 1. Homebrew

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

Add to `~/.zshrc`:
```bash
eval "$(/opt/homebrew/bin/brew shellenv)"
```

### 2. GPG (for signed commits)

```bash
brew install gnupg pinentry-mac
```

Generate a key:
```bash
gpg --full-generate-key
# Choose: RSA 4096, no expiry, your personal name + email
```

Configure pinentry:
```bash
echo "pinentry-program $(brew --prefix)/bin/pinentry-mac" >> ~/.gnupg/gpg-agent.conf
gpgconf --kill gpg-agent
```

Upload the public key to GitHub → Settings → GPG Keys:
```bash
gpg --armor --export <KEY_ID> | pbcopy
```

### 3. SSH Key (for GitHub)

```bash
ssh-keygen -t ed25519 -C "your@email.com" -f ~/.ssh/id_ed25519_personal
```

Configure `~/.ssh/config`:
```
Host github.com-personal
    HostName github.com
    User git
    IdentityFile ~/.ssh/id_ed25519_personal
    IdentitiesOnly yes
```

Upload the public key to GitHub → Settings → SSH Keys:
```bash
cat ~/.ssh/id_ed25519_personal.pub | pbcopy
```

Verify:
```bash
ssh -T git@github.com-personal
# Expected: Hi <username>! You've successfully authenticated...
```

### 4. Python (via uv)

```bash
brew install uv
uv python install 3.11
```

### 5. Docker Desktop

```bash
brew install --cask docker
```

Open **Docker.app** and wait until the menu bar whale icon shows "Docker Desktop is running".

### 6. Mosquitto CLI tools (for testing)

```bash
brew install mosquitto
```

These provide `mosquitto_pub` and `mosquitto_sub` for manual MQTT testing.
> Note: this installs the CLI tools only — the broker itself runs inside Docker.

---

## Project Setup

### Clone and install dependencies

```bash
git clone git@github.com-personal:marsyanggo/atf-validator.git
cd atf-validator
uv sync
```

### Git identity (repo-local, not global)

```bash
git config user.name "Your Name"
git config user.email "your@personal-email.com"
git config user.signingkey <GPG_KEY_ID>
git config commit.gpgsign true
git config gpg.program /opt/homebrew/bin/gpg
```

### Verify setup

```bash
# Python imports
uv run python -c "import paho.mqtt.client; import fastapi; import pydantic; import influxdb_client; print('OK')"

# Docker
docker compose version
```

---

## Start the infrastructure stack

```bash
docker compose up -d
```

Verify all three services:
```bash
# InfluxDB
curl localhost:8086/health
# Expected: {"status":"pass", ...}

# Mosquitto pub/sub roundtrip
mosquitto_sub -h localhost -p 1883 -t "atf/test" -C 1 &
mosquitto_pub -h localhost -p 1883 -t "atf/test" -m "hello-atf"
# Expected output: hello-atf

# Grafana
open http://localhost:3000
# Login: admin / atf-grafana-2026
```

---

## Commit guidelines

- **No commits between 09:00–18:00 on workdays** (legal compliance)
- Every commit is GPG-signed automatically
- Use personal email only — never company email

See [design_spec/atf-validator-phase1-spec.md](../design_spec/atf-validator-phase1-spec.md) §15 for full legal compliance rules.
