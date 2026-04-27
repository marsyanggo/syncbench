"""ATF test reporter — generates a markdown report from InfluxDB run data.

Usage:
    atf-report                          # report on latest run
    atf-report --run-id <run_id>        # report on specific run
    atf-report --out reports/my.md      # custom output path
"""

import argparse
import datetime
import math
import os
import sys

from influxdb_client import InfluxDBClient

from controller.atf_ctrl.metrics.influx_writer import (
    INFLUX_BUCKET, INFLUX_ORG, INFLUX_TOKEN, INFLUX_URL,
)
from controller.atf_ctrl.reporter.fairness import fairness_grade, jains_fairness_index


def _query(client: InfluxDBClient, flux: str) -> list[dict]:
    tables = client.query_api().query(flux, org=INFLUX_ORG)
    rows = []
    for table in tables:
        for record in table.records:
            rows.append(record.values)
    return rows


def fetch_run_summary(client: InfluxDBClient, run_id: str) -> list[dict]:
    fields = ["mean_mbps", "stdev_mbps", "p95_mbps", "retransmits", "sync_offset_ms"]
    # Query each field individually then merge by agent_id
    per_agent: dict[str, dict] = {}
    scenario_name = "unknown"
    for field in fields:
        flux = f'''
from(bucket: "{INFLUX_BUCKET}")
  |> range(start: -48h)
  |> filter(fn: (r) => r._measurement == "run_summary")
  |> filter(fn: (r) => r.run_id == "{run_id}")
  |> filter(fn: (r) => r._field == "{field}")
'''
        rows = _query(client, flux)
        for row in rows:
            agent = row.get("agent_id") or row.get("agent_id", "unknown")
            if not agent:
                continue
            per_agent.setdefault(agent, {"agent_id": agent, "run_id": run_id})
            per_agent[agent][field] = row.get("_value")
            if row.get("scenario"):
                scenario_name = row["scenario"]
    for v in per_agent.values():
        v["scenario"] = scenario_name
    return list(per_agent.values())


def fetch_latest_run_id(client: InfluxDBClient) -> str | None:
    flux = f'''
from(bucket: "{INFLUX_BUCKET}")
  |> range(start: -48h)
  |> filter(fn: (r) => r._measurement == "run_summary")
  |> filter(fn: (r) => r._field == "mean_mbps")
  |> group()
  |> sort(columns: ["_time"], desc: true)
  |> limit(n: 1)
'''
    rows = _query(client, flux)
    return rows[0].get("run_id") if rows else None


def fetch_throughput_samples(client: InfluxDBClient, run_id: str) -> dict[str, list[float]]:
    flux = f'''
from(bucket: "{INFLUX_BUCKET}")
  |> range(start: -48h)
  |> filter(fn: (r) => r._measurement == "throughput")
  |> filter(fn: (r) => r._field == "throughput_mbps")
  |> filter(fn: (r) => r.run_id == "{run_id}")
  |> filter(fn: (r) => exists r.agent_id and r.agent_id != "")
  |> group(columns: ["agent_id"])
'''
    rows = _query(client, flux)
    samples: dict[str, list[float]] = {}
    for row in rows:
        agent = row.get("agent_id", "unknown")
        val = row.get("_value", 0.0)
        samples.setdefault(agent, []).append(float(val))
    return samples


def generate_report(run_id: str, summary_rows: list[dict],
                    samples: dict[str, list[float]]) -> str:
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    agents = sorted({r.get("agent_id") for r in summary_rows if r.get("agent_id")})

    # Build per-agent stats
    stats: dict[str, dict] = {}
    for row in summary_rows:
        agent = row.get("agent_id")
        if not agent:
            continue
        stats[agent] = {
            "mean_mbps": row.get("mean_mbps", 0.0) or 0.0,
            "stdev_mbps": row.get("stdev_mbps", 0.0) or 0.0,
            "p95_mbps": row.get("p95_mbps", 0.0) or 0.0,
            "retransmits": int(row.get("retransmits", 0) or 0),
            "sync_offset_ms": int(row.get("sync_offset_ms", 0) or 0),
        }

    throughputs = [stats[a]["mean_mbps"] for a in agents if a in stats]
    total_mbps = sum(throughputs)
    jfi = jains_fairness_index(throughputs)
    grade = fairness_grade(jfi)
    scenario = summary_rows[0].get("scenario", "unknown") if summary_rows else "unknown"

    lines = [
        f"# ATF Test Report",
        f"",
        f"**Run ID:** `{run_id}`  ",
        f"**Scenario:** {scenario}  ",
        f"**Generated:** {now}  ",
        f"",
        f"---",
        f"",
        f"## Summary",
        f"",
        f"| Metric | Value |",
        f"|---|---|",
        f"| STAs | {len(agents)} |",
        f"| Total throughput | {total_mbps:.1f} Mbps |",
        f"| Jain's Fairness Index | **{jfi:.3f}** |",
        f"| Fairness grade | **{grade}** |",
        f"",
        f"---",
        f"",
        f"## Per-STA Results",
        f"",
        f"| STA | Avg (Mbps) | Stdev | p95 | Retransmits | Sync Offset |",
        f"|---|---|---|---|---|---|",
    ]

    for agent in agents:
        s = stats.get(agent, {})
        lines.append(
            f"| {agent} "
            f"| {s.get('mean_mbps', 0):.1f} "
            f"| ±{s.get('stdev_mbps', 0):.1f} "
            f"| {s.get('p95_mbps', 0):.1f} "
            f"| {s.get('retransmits', 0)} "
            f"| {s.get('sync_offset_ms', 0)} ms |"
        )

    # ASCII throughput bar chart
    lines += ["", "---", "", "## Throughput Distribution", ""]
    max_val = max(throughputs) if throughputs else 1
    bar_width = 40
    for agent in agents:
        val = stats.get(agent, {}).get("mean_mbps", 0)
        bar_len = int(val / max_val * bar_width)
        pct = val / total_mbps * 100 if total_mbps else 0
        lines.append(f"`{agent:<15}` {'█' * bar_len}{'░' * (bar_width - bar_len)} {val:.1f} Mbps ({pct:.1f}%)")

    # Jain's FI explanation
    lines += [
        "",
        "---",
        "",
        "## Fairness Analysis",
        "",
        f"**Jain's Fairness Index (JFI) = {jfi:.4f}**",
        "",
        f"JFI = (Σxi)² / (n × Σxi²) where xi = per-STA average throughput",
        f"",
        f"| JFI Range | Grade | Meaning |",
        f"|---|---|---|",
        f"| 0.95 – 1.00 | Excellent | Near-perfect fairness |",
        f"| 0.80 – 0.95 | Good | Minor imbalance |",
        f"| 0.60 – 0.80 | Fair | Moderate imbalance |",
        f"| < 0.60 | Poor | Severe imbalance |",
        f"",
        f"This run: **{grade}** (JFI = {jfi:.3f})",
    ]

    if len(agents) > 1:
        min_t = min(throughputs)
        max_t = max(throughputs)
        lines += [
            "",
            f"Min/Max throughput ratio: {min_t:.1f} / {max_t:.1f} = **{min_t/max_t:.2f}**",
            f"(1.00 = perfectly equal, 0.00 = one STA gets nothing)",
        ]

    lines += ["", "---", "", f"*Generated by atf-report — ATF Validator*"]
    return "\n".join(lines)


def main() -> None:
    p = argparse.ArgumentParser(description="Generate ATF test report from InfluxDB")
    p.add_argument("--run-id", help="Specific run_id (default: latest)")
    p.add_argument("--out", help="Output path (default: reports/<run_id>.md)")
    args = p.parse_args()

    client = InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG)

    run_id = args.run_id or fetch_latest_run_id(client)
    if not run_id:
        print("ERROR: no run data found in InfluxDB (last 48h)", file=sys.stderr)
        sys.exit(1)

    print(f"Generating report for run: {run_id}")
    summary_rows = fetch_run_summary(client, run_id)
    if not summary_rows:
        print(f"ERROR: no run_summary data for run_id={run_id}", file=sys.stderr)
        sys.exit(1)

    samples = fetch_throughput_samples(client, run_id)
    report = generate_report(run_id, summary_rows, samples)

    out_path = args.out or f"reports/{run_id}.md"
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w") as f:
        f.write(report)

    print(f"Report written: {out_path}")

    # Also print summary to stdout
    for line in report.split("\n"):
        if line.startswith("| ") or "Jain" in line or "grade" in line.lower() or "Total" in line:
            print(line)


if __name__ == "__main__":
    main()
