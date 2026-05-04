#!/usr/bin/env python3
"""Sync agent code to RPi nodes and restart the atf-agent service.

Uses paramiko (pure-Python SSH) so no sshpass or key setup needed — password
auth works out of the box. File transfer is done via SFTP (recursive upload).

Usage:
    python scripts/sync-agents.py                        # sync + restart all
    python scripts/sync-agents.py rpi-sta-01 rpi-sta-02  # specific nodes
    python scripts/sync-agents.py --no-restart           # code only, no restart
    python scripts/sync-agents.py --deps                 # also run uv sync on remote
    python scripts/sync-agents.py --status               # check service status
    python scripts/sync-agents.py --dry-run              # print actions, no changes

Configuration: edit AGENTS / SSH_PASSWORD / REMOTE_DIR below.
Or set env vars: ATF_AGENTS=rpi-sta-01,rpi-sta-02  ATF_SSH_PASS=1
"""

import argparse
import os
import stat
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import paramiko

# ── Inventory ─────────────────────────────────────────────────────────────────
AGENTS: dict[str, str] = {
    "rpi-sta-01": "rpi-sta-01.local",
    "rpi-sta-02": "rpi-sta-02.local",
    "rpi-sta-03": "rpi-sta-03.local",
    "rpi-sta-04": "rpi-sta-04.local",
    "rpi-sta-05": "rpi-sta-05.local",
    # "linux-nb-01": "linux-nb-01.local",
}

SSH_USER     = "mars"
SSH_PASSWORD = os.environ.get("ATF_SSH_PASS", "1")
REMOTE_DIR   = "/home/mars/atf-validator"  # absolute path (no ~ with SFTP)

# Local dirs/files to sync (relative to repo root)
SYNC_PATHS = ["agent", "shared", "pyproject.toml", "uv.lock"]

# Remote paths to remove that are not in SYNC_PATHS (prevents stale files)
# Anything else in REMOTE_DIR is left untouched.


# ── Result tracking ───────────────────────────────────────────────────────────
@dataclass
class NodeResult:
    agent_id: str
    ok: bool = False
    steps: list[str] = field(default_factory=list)
    error: str = ""
    elapsed: float = 0.0


# ── SSH / SFTP helpers ────────────────────────────────────────────────────────
def _connect(host: str) -> paramiko.SSHClient:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        host,
        username=SSH_USER,
        password=SSH_PASSWORD,
        timeout=15,
        banner_timeout=15,
        auth_timeout=15,
    )
    return client


def _exec(client: paramiko.SSHClient, cmd: str) -> tuple[int, str]:
    _, stdout, stderr = client.exec_command(cmd, timeout=60)
    out = stdout.read().decode().strip()
    err = stderr.read().decode().strip()
    rc  = stdout.channel.recv_exit_status()
    return rc, (out + ("\n" + err if err else "")).strip()


def _sftp_mkdir_p(sftp: paramiko.SFTPClient, remote_dir: str) -> None:
    """Create remote directory and all parents (like mkdir -p)."""
    parts = Path(remote_dir).parts
    cur = ""
    for part in parts:
        cur = str(Path(cur) / part) if cur else part
        try:
            sftp.stat(cur)
        except FileNotFoundError:
            sftp.mkdir(cur)


def _sftp_upload_dir(sftp: paramiko.SFTPClient, local: Path, remote: str) -> int:
    """Recursively upload local dir to remote path. Returns file count."""
    _sftp_mkdir_p(sftp, remote)
    count = 0
    # Build set of local names for later cleanup
    local_names = {p.name for p in local.iterdir()}

    # Remove remote files not present locally
    try:
        for entry in sftp.listdir_attr(remote):
            if entry.filename not in local_names:
                rpath = f"{remote}/{entry.filename}"
                if stat.S_ISDIR(entry.st_mode):
                    _sftp_rmdir_r(sftp, rpath)
                else:
                    sftp.remove(rpath)
    except Exception:
        pass

    for item in sorted(local.iterdir()):
        if item.name in ("__pycache__", ".venv", "*.pyc") or item.suffix == ".pyc":
            continue
        rpath = f"{remote}/{item.name}"
        if item.is_dir():
            count += _sftp_upload_dir(sftp, item, rpath)
        else:
            sftp.put(str(item), rpath)
            count += 1
    return count


def _sftp_rmdir_r(sftp: paramiko.SFTPClient, remote: str) -> None:
    for entry in sftp.listdir_attr(remote):
        rpath = f"{remote}/{entry.filename}"
        if stat.S_ISDIR(entry.st_mode):
            _sftp_rmdir_r(sftp, rpath)
        else:
            sftp.remove(rpath)
    sftp.rmdir(remote)


# ── Per-node sync ─────────────────────────────────────────────────────────────
def sync_node(
    agent_id: str,
    host: str,
    repo_root: Path,
    restart: bool,
    deps: bool,
    dry_run: bool,
    lock: threading.Lock,
) -> NodeResult:
    res = NodeResult(agent_id=agent_id)
    t0  = time.monotonic()

    def log(msg: str) -> None:
        with lock:
            print(f"  [{agent_id}] {msg}")

    if dry_run:
        log(f"[dry-run] would sync {SYNC_PATHS} → {host}:{REMOTE_DIR}")
        if restart:
            log(f"[dry-run] would restart atf-agent on {host}")
        res.ok = True
        res.elapsed = time.monotonic() - t0
        return res

    # Connect
    try:
        client = _connect(host)
    except Exception as exc:
        res.error = f"SSH connect failed: {exc}"
        log(f"✗ {res.error}")
        res.elapsed = time.monotonic() - t0
        return res

    try:
        sftp = client.open_sftp()

        # Ensure remote base dir exists
        _sftp_mkdir_p(sftp, REMOTE_DIR)

        # Upload each path
        total_files = 0
        for name in SYNC_PATHS:
            local_path = repo_root / name
            if not local_path.exists():
                log(f"⚠ local path missing: {name} — skipped")
                continue
            remote_path = f"{REMOTE_DIR}/{name}"
            if local_path.is_dir():
                n = _sftp_upload_dir(sftp, local_path, remote_path)
                total_files += n
            else:
                sftp.put(str(local_path), remote_path)
                total_files += 1

        sftp.close()
        res.steps.append("upload")
        log(f"✓ upload ({total_files} files)")

        # Optional: uv sync
        if deps:
            rc, out = _exec(client, f"cd {REMOTE_DIR} && ~/.local/bin/uv sync --quiet 2>&1")
            if rc != 0:
                res.error = f"uv sync failed: {out[:200]}"
                log(f"✗ uv sync — {res.error}")
                res.elapsed = time.monotonic() - t0
                return res
            res.steps.append("uv sync")
            log("✓ uv sync")

        # Restart service
        if restart:
            rc, out = _exec(client, "sudo systemctl restart atf-agent 2>&1")
            if rc != 0:
                res.error = f"restart failed (rc={rc}): {out[:200]}"
                log(f"✗ restart — {res.error}")
                res.elapsed = time.monotonic() - t0
                return res

            rc, status = _exec(client, "systemctl is-active atf-agent 2>&1")
            if status.strip() == "active":
                res.steps.append("restart")
                log("✓ restart → active")
            else:
                res.error = f"service not active: {status}"
                log(f"✗ {res.error}")
                res.elapsed = time.monotonic() - t0
                return res

    except Exception as exc:
        res.error = f"error: {exc}"
        log(f"✗ {res.error}")
        res.elapsed = time.monotonic() - t0
        return res
    finally:
        client.close()

    res.ok = True
    res.elapsed = time.monotonic() - t0
    return res


# ── Status check ─────────────────────────────────────────────────────────────
def _check_status(selected: dict[str, str]) -> int:
    lock  = threading.Lock()
    rows: dict[str, str] = {}

    def check_one(agent_id: str, host: str) -> None:
        try:
            client = _connect(host)
            rc, out = _exec(
                client,
                "systemctl is-active atf-agent; "
                "systemctl show atf-agent --property=MainPID --value 2>/dev/null; "
                "journalctl -u atf-agent -n 1 --no-pager -o short 2>/dev/null | tail -1",
            )
            client.close()
            lines = out.strip().split("\n") if out.strip() else []
            state = lines[0].strip() if lines else "unknown"
            pid   = lines[1].strip() if len(lines) > 1 else "—"
            last  = lines[2].strip() if len(lines) > 2 else ""
            icon  = "✓" if state == "active" else "✗"
            row   = f"{icon} {state:10s} pid={pid:6s}  {last[-60:]}"
        except Exception as exc:
            row = f"✗ unreachable: {exc}"
        with lock:
            rows[agent_id] = row

    threads = [
        threading.Thread(target=check_one, args=(a, h), daemon=True)
        for a, h in selected.items()
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    print()
    for agent_id, row in rows.items():
        print(f"  {agent_id:20s} {row}")
    print()
    return 0 if all("✓" in r for r in rows.values()) else 1


# ── Main ──────────────────────────────────────────────────────────────────────
def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("agents", nargs="*", help="Agent IDs to sync (default: all)")
    parser.add_argument("--no-restart", action="store_true", help="Upload only, skip service restart")
    parser.add_argument("--deps",       action="store_true", help="Run uv sync on remote after upload")
    parser.add_argument("--dry-run",    action="store_true", help="Print actions without executing")
    parser.add_argument("--list",       action="store_true", help="List configured agents and exit")
    parser.add_argument("--status",     action="store_true", help="Check service status (no sync)")
    args = parser.parse_args()

    env_agents = os.environ.get("ATF_AGENTS", "")
    if env_agents:
        selected = {a: AGENTS[a] for a in env_agents.split(",") if a in AGENTS}
    elif args.agents:
        missing = [a for a in args.agents if a not in AGENTS]
        if missing:
            print(f"Unknown agent(s): {missing}  Known: {list(AGENTS)}")
            return 1
        selected = {a: AGENTS[a] for a in args.agents}
    else:
        selected = dict(AGENTS)

    if args.list:
        for agent_id, host in AGENTS.items():
            print(f"  {agent_id:20s} → {SSH_USER}@{host}")
        return 0

    if args.status:
        return _check_status(selected)

    repo_root = Path(__file__).parent.parent
    ops = ["upload"] + (["uv sync"] if args.deps else []) + ([] if args.no_restart else ["restart"])
    print(f"\nSyncing {len(selected)} agent(s): {' + '.join(ops)}")
    print(f"  {', '.join(selected)}\n")

    lock    = threading.Lock()
    results: dict[str, NodeResult] = {}

    threads = [
        threading.Thread(
            target=lambda aid=aid, host=host: results.__setitem__(
                aid,
                sync_node(
                    agent_id=aid, host=host, repo_root=repo_root,
                    restart=not args.no_restart, deps=args.deps,
                    dry_run=args.dry_run, lock=lock,
                ),
            ),
            daemon=True,
        )
        for aid, host in selected.items()
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    ok_count = sum(1 for r in results.values() if r.ok)
    print()
    print("─" * 50)
    for aid, r in results.items():
        status = f"✓ {r.elapsed:.1f}s" if r.ok else f"✗ {r.error}"
        print(f"  {aid:20s} {status}")
    print("─" * 50)
    print(f"  {ok_count}/{len(selected)} OK\n")
    return 0 if ok_count == len(selected) else 1


if __name__ == "__main__":
    sys.exit(main())
