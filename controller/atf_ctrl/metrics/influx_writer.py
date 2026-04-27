"""Write ATF run results to InfluxDB.

Two measurements:
  throughput  — per-interval time series (one point per second per STA)
  run_summary — per-run aggregate (one point per STA per run)
"""

import logging
import os

from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS

from controller.atf_ctrl.orchestrator import AgentResult, RunResult

logger = logging.getLogger("atf.metrics")

INFLUX_URL = os.environ.get("INFLUXDB_URL", "http://localhost:8086")
INFLUX_TOKEN = os.environ.get("INFLUXDB_TOKEN", "")
INFLUX_ORG = os.environ.get("INFLUXDB_ORG", "atf")
INFLUX_BUCKET = os.environ.get("INFLUXDB_BUCKET", "atf_metrics")

if not INFLUX_TOKEN:
    raise RuntimeError(
        "INFLUXDB_TOKEN is not set. "
        "Get your token from http://localhost:8086 → Data → API Tokens, "
        "then: export INFLUXDB_TOKEN=<your-token>"
    )


class InfluxWriter:
    def __init__(self) -> None:
        self._client = InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG)
        self._write = self._client.write_api(write_options=SYNCHRONOUS)

    def write_run(self, run_result: RunResult, scenario_name: str) -> None:
        points = []

        for agent_id, ar in run_result.agent_results.items():
            if ar.status != "complete":
                continue

            # Per-interval samples already written to InfluxDB in real-time via live subscription.
            # Only write run_summary here.
            summary = ar.raw.get("summary", {})
            p = (
                Point("run_summary")
                .tag("run_id", run_result.run_id)
                .tag("agent_id", agent_id)
                .tag("scenario", scenario_name)
                .field("mean_mbps", float(ar.throughput_mean_mbps or 0))
                .field("stdev_mbps", float(summary.get("throughput_stdev_mbps") or 0))
                .field("p95_mbps", float(summary.get("throughput_p95_mbps") or 0))
                .field("retransmits", int(ar.total_retransmits or 0))
                .field("sync_offset_ms", int(ar.sync_offset_ms or 0))
            )
            points.append(p)

        if points:
            self._write.write(bucket=INFLUX_BUCKET, record=points)
            logger.info("Wrote %d points to InfluxDB (run %s)", len(points), run_result.run_id)

    def close(self) -> None:
        self._client.close()
