"""AP collector — reads per-station airtime from OpenWrt mt76 debugfs.

Architecture:
  1. Subscribe to atf/agent/+/status (retained) → build wifi_mac → agent_id map
  2. Periodically (every interval_sec):
     a. SSH into AP, list /sys/kernel/debug/ieee80211/{phy}/netdev:{iface}/stations/
     b. For each station MAC, read airtime file (RX/TX cumulative microseconds)
     c. Compute delta vs. previous read
     d. Write to InfluxDB measurement `ap_airtime` tagged with agent_id (or MAC if unknown)

debugfs file format (mt76):
    RX: 577039977 us
    TX: 88128273 us
    Weight: 256
    Deficit: VO: 1817 us VI: 256 us BE: -115 us BK: 256 us
"""

import argparse
import logging
import re
import signal
import subprocess
import sys
import threading
import time

from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS

from controller.atf_ctrl.metrics.influx_writer import (
    INFLUX_BUCKET, INFLUX_ORG, INFLUX_TOKEN, INFLUX_URL,
)
from shared.mqtt_bus import MQTTBus

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("atf.ap_collector")


_RX_RE = re.compile(r"RX:\s*(\d+)\s*us")
_TX_RE = re.compile(r"TX:\s*(\d+)\s*us")


class APCollector:
    def __init__(
        self,
        ap_host: str,
        ap_user: str,
        phy: str,
        iface: str,
        interval_sec: float,
        broker: str,
    ) -> None:
        self._ssh_target = f"{ap_user}@{ap_host}"
        self._stations_dir = f"/sys/kernel/debug/ieee80211/{phy}/netdev:{iface}/stations"
        self._interval = interval_sec
        self._mac_to_agent: dict[str, str] = {}
        self._prev_samples: dict[str, tuple[int, int, float]] = {}  # mac → (rx_us, tx_us, ts)
        self._stop = threading.Event()

        # MQTT for agent_id discovery
        self._bus = MQTTBus()
        self._bus.connect(broker, 1883, client_id="atf-ap-collector")
        self._bus.subscribe("atf/agent/+/status", self._on_status, qos=1)
        self._bus.loop_start()

        # InfluxDB
        self._influx = InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG)
        self._write = self._influx.write_api(write_options=SYNCHRONOUS)

    def _on_status(self, topic: str, payload: dict) -> None:
        mac = payload.get("wifi_mac")
        agent_id = payload.get("agent_id")
        if mac and agent_id:
            mac = mac.lower()
            if self._mac_to_agent.get(mac) != agent_id:
                logger.info("Map %s → %s", mac, agent_id)
                self._mac_to_agent[mac] = agent_id

    def run(self) -> None:
        logger.info("AP collector started (target=%s, interval=%.1fs)", self._ssh_target, self._interval)
        # Give MQTT a moment to receive retained status messages
        time.sleep(2.0)
        logger.info("Initial MAC map: %s", self._mac_to_agent or "(empty — agents will be tagged by MAC until status arrives)")

        while not self._stop.is_set():
            t0 = time.time()
            try:
                self._collect_once()
            except Exception as exc:
                logger.warning("collect failed: %s", exc)
            elapsed = time.time() - t0
            sleep_for = max(0.1, self._interval - elapsed)
            self._stop.wait(sleep_for)

    def stop(self) -> None:
        self._stop.set()
        self._bus.loop_stop()
        self._bus.disconnect()
        self._influx.close()

    def _collect_once(self) -> None:
        macs = self._list_stations()
        if not macs:
            return

        # Single SSH call: emit MAC<TAB>file_content for each station
        cat_cmd = "; ".join(
            f"echo '===MAC={mac}==='; cat {self._stations_dir}/{mac}/airtime"
            for mac in macs
        )
        out = subprocess.check_output(
            ["ssh", "-o", "StrictHostKeyChecking=no", self._ssh_target, cat_cmd],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )

        now = time.time()
        points = []
        # Parse: split on the marker, each block starts with MAC=xx... then content
        blocks = re.split(r"===MAC=([0-9a-f:]{17})===", out)
        # blocks = ['', mac1, body1, mac2, body2, ...]
        it = iter(blocks[1:])
        for mac in it:
            body = next(it, "")
            mac = mac.strip().lower()
            rx_m = _RX_RE.search(body)
            tx_m = _TX_RE.search(body)
            if not (rx_m and tx_m):
                logger.debug("no RX/TX in body for %s: %r", mac, body[:80])
                continue
            rx_us = int(rx_m.group(1))
            tx_us = int(tx_m.group(1))

            prev = self._prev_samples.get(mac)
            self._prev_samples[mac] = (rx_us, tx_us, now)
            if not prev:
                continue  # need 2 samples for delta

            prev_rx, prev_tx, prev_t = prev
            dt = now - prev_t
            if dt <= 0:
                continue
            rx_pct = max(0.0, (rx_us - prev_rx) / 1_000_000 / dt * 100)
            tx_pct = max(0.0, (tx_us - prev_tx) / 1_000_000 / dt * 100)

            agent_id = self._mac_to_agent.get(mac, mac)
            p = (
                Point("ap_airtime")
                .tag("agent_id", agent_id)
                .tag("mac", mac)
                .field("tx_pct", tx_pct)
                .field("rx_pct", rx_pct)
                .time(int(now * 1e9), WritePrecision.NS)
            )
            points.append(p)

        if points:
            self._write.write(bucket=INFLUX_BUCKET, record=points)
            logger.info("Wrote %d airtime points", len(points))

    def _list_stations(self) -> list[str]:
        out = subprocess.check_output(
            ["ssh", "-o", "StrictHostKeyChecking=no", self._ssh_target,
             f"ls {self._stations_dir}"],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
        return [line.strip() for line in out.splitlines() if ":" in line]


def main() -> None:
    p = argparse.ArgumentParser(description="ATF AP airtime collector")
    p.add_argument("--ap", default="192.168.1.1", help="AP IP address")
    p.add_argument("--user", default="root", help="SSH user (default: root)")
    p.add_argument("--phy", default="phy1", help="phy name (default: phy1 for 5GHz)")
    p.add_argument("--iface", default="phy1-ap0", help="netdev interface (default: phy1-ap0)")
    p.add_argument("--interval", type=float, default=1.0, help="Sample interval in seconds")
    p.add_argument("--broker", default="localhost", help="MQTT broker host")
    args = p.parse_args()

    collector = APCollector(
        ap_host=args.ap, ap_user=args.user,
        phy=args.phy, iface=args.iface,
        interval_sec=args.interval, broker=args.broker,
    )

    def _shutdown(sig, frame):
        logger.info("Shutting down")
        collector.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)
    collector.run()


if __name__ == "__main__":
    main()
