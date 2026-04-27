# Security Policy

## Scope

ATF Validator is a research/validation framework intended for controlled lab environments. It is not designed for production deployment.

**Known non-production aspects:**
- MQTT broker runs without authentication (allow_anonymous)
- InfluxDB admin credentials are stored in plain text in docker-compose.yml
- SSH keys are used without a passphrase for automation convenience
- The AP collector SSHes into the router with root credentials

These are acceptable tradeoffs for a lab testbed. Do not deploy this framework on untrusted networks.

## Reporting a vulnerability

If you find a security issue relevant to the framework's design (e.g., a way to inject malicious MQTT payloads that could affect the host system), please open a GitHub issue marked **[SECURITY]**.

For vulnerabilities in dependencies (paho-mqtt, influxdb-client, fastapi, etc.), report directly to those upstream projects.

## Supported versions

Only the latest commit on `main` is supported.
