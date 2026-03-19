from __future__ import annotations

import csv
import json
import math
import os
from pathlib import Path
from statistics import mean

import configargparse

os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib")
os.environ.setdefault("XDG_CACHE_HOME", "/tmp/.cache")

import matplotlib.pyplot as plt


def load_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="") as handle:
        return list(csv.DictReader(handle))


def parse_float(value: str) -> float:
    return float(value) if value else 0.0


def parse_int(value: str) -> int:
    return int(value) if value else 0


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * pct
    low = math.floor(rank)
    high = math.ceil(rank)
    if low == high:
        return ordered[low]
    weight = rank - low
    return ordered[low] * (1 - weight) + ordered[high] * weight


def safe_mean(values: list[float]) -> float:
    return mean(values) if values else 0.0


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    if not rows:
        return
    fieldnames: list[str] = []
    for row in rows:
        for key in row.keys():
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def bucket_rows(rows: list[dict[str, str]], window: float) -> dict[int, list[dict[str, str]]]:
    buckets: dict[int, list[dict[str, str]]] = {}
    for row in rows:
        bucket = int(parse_float(row["t"]) // window)
        buckets.setdefault(bucket, []).append(row)
    return buckets


def average_active_nodes_by_bucket(
    snapshot_samples: list[dict[str, str]],
    window: float,
) -> dict[int, float]:
    buckets = bucket_rows(snapshot_samples, window)
    averages: dict[int, float] = {}
    for bucket, rows in buckets.items():
        averages[bucket] = safe_mean([parse_int(row["active_nodes"]) for row in rows])
    return averages


def compute_time_weighted_queue_stats(
    queue_samples: list[dict[str, str]],
) -> tuple[list[dict[str, object]], dict[str, object]]:
    node_rows: list[dict[str, object]] = []
    node_to_samples: dict[str, list[dict[str, str]]] = {}
    for row in queue_samples:
        node_to_samples.setdefault(row["node"], []).append(row)

    weighted_averages: list[float] = []
    max_values: list[int] = []
    non_empty_fractions: list[float] = []

    for node in sorted(node_to_samples):
        samples = sorted(node_to_samples[node], key=lambda row: parse_float(row["t"]))
        if not samples:
            continue

        weighted_area = 0.0
        non_empty_time = 0.0
        max_queue_len = 0

        for current, nxt in zip(samples, samples[1:]):
            current_t = parse_float(current["t"])
            next_t = parse_float(nxt["t"])
            queue_len = parse_int(current["queue_len"])
            duration = max(0.0, next_t - current_t)
            weighted_area += queue_len * duration
            if queue_len > 0:
                non_empty_time += duration
            max_queue_len = max(max_queue_len, queue_len)

        start_time = parse_float(samples[0]["t"])
        end_time = parse_float(samples[-1]["t"])
        active_duration = max(0.0, end_time - start_time)
        avg_queue_len = weighted_area / active_duration if active_duration else 0.0
        fraction_non_empty = non_empty_time / active_duration if active_duration else 0.0

        weighted_averages.append(avg_queue_len)
        max_values.append(max_queue_len)
        non_empty_fractions.append(fraction_non_empty)
        node_rows.append(
            {
                "node": node,
                "active_duration": round(active_duration, 3),
                "time_weighted_avg_queue_len": round(avg_queue_len, 3),
                "max_queue_len": max_queue_len,
                "fraction_time_non_empty": round(fraction_non_empty, 3),
            }
        )

    summary = {
        "avg_time_weighted_queue_len": round(safe_mean(weighted_averages), 3),
        "p95_time_weighted_queue_len": round(percentile(weighted_averages, 0.95), 3),
        "max_queue_len": max(max_values, default=0),
        "avg_fraction_time_non_empty": round(safe_mean(non_empty_fractions), 3),
    }
    return node_rows, summary


def analyze_metadata_growth(
    sends: list[dict[str, str]],
    window: float,
    output_dir: Path,
) -> dict[str, object]:
    rows: list[dict[str, object]] = []
    for bucket, bucket_rows_ in sorted(bucket_rows(sends, window).items()):
        meta_sizes = [parse_int(row["meta_size"]) for row in bucket_rows_]
        rows.append(
            {
                "window_start": bucket * window,
                "window_end": (bucket + 1) * window,
                "send_count": len(bucket_rows_),
                "avg_metadata_size": round(safe_mean(meta_sizes), 3),
                "p95_metadata_size": round(percentile(meta_sizes, 0.95), 3),
                "max_metadata_size": max(meta_sizes, default=0),
            }
        )

    write_csv(output_dir / "metadata_growth.csv", rows)

    if rows:
        xs = [row["window_start"] for row in rows]
        plt.figure(figsize=(9, 5))
        plt.plot(xs, [row["avg_metadata_size"] for row in rows], label="avg")
        plt.plot(xs, [row["p95_metadata_size"] for row in rows], label="p95")
        plt.xlabel("Simulation time")
        plt.ylabel("Metadata entries")
        plt.title("Metadata Growth Over Time")
        plt.legend()
        plt.tight_layout()
        plt.savefig(output_dir / "metadata_growth.png", dpi=150)
        plt.close()

    all_meta_sizes = [parse_int(row["meta_size"]) for row in sends]
    return {
        "avg_metadata_size": round(safe_mean(all_meta_sizes), 3),
        "p95_metadata_size": round(percentile(all_meta_sizes, 0.95), 3),
        "max_metadata_size": max(all_meta_sizes, default=0),
    }


def analyze_metadata_representation(
    sends: list[dict[str, str]],
    snapshot_samples: list[dict[str, str]],
    window: float,
    output_dir: Path,
) -> dict[str, object]:
    rows: list[dict[str, object]] = []
    active_nodes = average_active_nodes_by_bucket(snapshot_samples, window)

    for bucket, bucket_rows_ in sorted(bucket_rows(sends, window).items()):
        metadata_bytes = [parse_int(row["metadata_bytes"]) for row in bucket_rows_]
        context_entries = [parse_int(row["context_entries"]) for row in bucket_rows_]
        avg_metadata_bytes = safe_mean(metadata_bytes)
        avg_active_nodes = active_nodes.get(bucket, 0.0)
        rows.append(
            {
                "window_start": bucket * window,
                "window_end": (bucket + 1) * window,
                "send_count": len(bucket_rows_),
                "avg_metadata_bytes": round(avg_metadata_bytes, 3),
                "p95_metadata_bytes": round(percentile(metadata_bytes, 0.95), 3),
                "avg_context_entries": round(safe_mean(context_entries), 3),
                "avg_active_nodes": round(avg_active_nodes, 3),
                "avg_metadata_bytes_per_active_node": round(
                    avg_metadata_bytes / avg_active_nodes if avg_active_nodes else 0.0, 3
                ),
            }
        )

    write_csv(output_dir / "metadata_representation.csv", rows)

    if rows:
        xs = [row["window_start"] for row in rows]
        plt.figure(figsize=(9, 5))
        plt.plot(xs, [row["avg_metadata_bytes"] for row in rows], label="avg metadata bytes")
        plt.plot(
            xs,
            [row["avg_metadata_bytes_per_active_node"] for row in rows],
            label="bytes per active node",
        )
        plt.xlabel("Simulation time")
        plt.ylabel("Metadata bytes")
        plt.title("Metadata Representation Over Time")
        plt.legend()
        plt.tight_layout()
        plt.savefig(output_dir / "metadata_representation.png", dpi=150)
        plt.close()

    all_metadata_bytes = [parse_int(row["metadata_bytes"]) for row in sends]
    all_context_entries = [parse_int(row["context_entries"]) for row in sends]
    normalized = [float(row["avg_metadata_bytes_per_active_node"]) for row in rows]
    return {
        "avg_metadata_bytes": round(safe_mean(all_metadata_bytes), 3),
        "p95_metadata_bytes": round(percentile(all_metadata_bytes, 0.95), 3),
        "avg_context_entries": round(safe_mean(all_context_entries), 3),
        "avg_metadata_bytes_per_active_node": round(safe_mean(normalized), 3),
    }


def analyze_stale_metadata(
    sends: list[dict[str, str]],
    window: float,
    output_dir: Path,
) -> dict[str, object]:
    rows: list[dict[str, object]] = []
    for bucket, bucket_rows_ in sorted(bucket_rows(sends, window).items()):
        stale_entries = [parse_int(row["stale_metadata_entries"]) for row in bucket_rows_]
        stale_fractions = [parse_float(row["stale_metadata_fraction"]) for row in bucket_rows_]
        rows.append(
            {
                "window_start": bucket * window,
                "window_end": (bucket + 1) * window,
                "send_count": len(bucket_rows_),
                "avg_stale_metadata_entries": round(safe_mean(stale_entries), 3),
                "p95_stale_metadata_entries": round(percentile(stale_entries, 0.95), 3),
                "avg_stale_metadata_fraction": round(safe_mean(stale_fractions), 3),
            }
        )

    write_csv(output_dir / "stale_metadata_over_time.csv", rows)

    if rows:
        xs = [row["window_start"] for row in rows]
        plt.figure(figsize=(9, 5))
        plt.plot(xs, [row["avg_stale_metadata_entries"] for row in rows], label="stale entries")
        plt.plot(xs, [row["avg_stale_metadata_fraction"] for row in rows], label="stale fraction")
        plt.xlabel("Simulation time")
        plt.ylabel("Stale metadata")
        plt.title("Stale Metadata Under Churn")
        plt.legend()
        plt.tight_layout()
        plt.savefig(output_dir / "stale_metadata_over_time.png", dpi=150)
        plt.close()

    all_stale_entries = [parse_int(row["stale_metadata_entries"]) for row in sends]
    all_stale_fractions = [parse_float(row["stale_metadata_fraction"]) for row in sends]
    return {
        "avg_stale_metadata_entries": round(safe_mean(all_stale_entries), 3),
        "p95_stale_metadata_entries": round(percentile(all_stale_entries, 0.95), 3),
        "avg_stale_metadata_fraction": round(safe_mean(all_stale_fractions), 3),
    }


def analyze_churn(
    joins: list[dict[str, str]],
    leaves: list[dict[str, str]],
    snapshot_samples: list[dict[str, str]],
    window: float,
    output_dir: Path,
) -> dict[str, object]:
    rows: list[dict[str, object]] = []
    join_buckets = bucket_rows(joins, window)
    leave_buckets = bucket_rows(leaves, window)
    snapshot_buckets = bucket_rows(snapshot_samples, window)
    all_buckets = sorted(set(join_buckets) | set(leave_buckets) | set(snapshot_buckets))

    for bucket in all_buckets:
        bucket_joins = join_buckets.get(bucket, [])
        bucket_leaves = leave_buckets.get(bucket, [])
        snapshots = snapshot_buckets.get(bucket, [])
        avg_active_nodes = safe_mean([parse_int(row["active_nodes"]) for row in snapshots])
        total_churn_events = len(bucket_joins) + len(bucket_leaves)
        rows.append(
            {
                "window_start": bucket * window,
                "window_end": (bucket + 1) * window,
                "join_count": len(bucket_joins),
                "leave_count": len(bucket_leaves),
                "total_churn_events": total_churn_events,
                "churn_events_per_time": round(total_churn_events / window, 3),
                "avg_active_nodes": round(avg_active_nodes, 3),
                "churn_per_active_node": round(
                    total_churn_events / avg_active_nodes if avg_active_nodes else 0.0, 3
                ),
            }
        )

    write_csv(output_dir / "churn_over_time.csv", rows)

    if rows:
        xs = [row["window_start"] for row in rows]
        fig, ax1 = plt.subplots(figsize=(9, 5))
        ax1.plot(xs, [row["avg_active_nodes"] for row in rows], color="tab:blue")
        ax1.set_xlabel("Simulation time")
        ax1.set_ylabel("Active nodes", color="tab:blue")
        ax1.tick_params(axis="y", labelcolor="tab:blue")

        ax2 = ax1.twinx()
        ax2.plot(xs, [row["churn_events_per_time"] for row in rows], color="tab:red")
        ax2.set_ylabel("Churn events / time", color="tab:red")
        ax2.tick_params(axis="y", labelcolor="tab:red")

        fig.suptitle("Cluster Size and Churn Over Time")
        fig.tight_layout()
        fig.savefig(output_dir / "churn_over_time.png", dpi=150)
        plt.close(fig)

    return {
        "total_joins": len(joins),
        "total_leaves": len(leaves),
        "total_churn_events": len(joins) + len(leaves),
        "avg_active_nodes": round(
            safe_mean([float(row["avg_active_nodes"]) for row in rows]), 3
        ),
        "avg_churn_per_active_node": round(
            safe_mean([float(row["churn_per_active_node"]) for row in rows]), 3
        ),
        "peak_churn_events_per_time": round(
            max((float(row["churn_events_per_time"]) for row in rows), default=0.0), 3
        ),
    }


def analyze_queue_lengths(
    queue_samples: list[dict[str, str]],
    snapshot_samples: list[dict[str, str]],
    output_dir: Path,
) -> dict[str, object]:
    rows: list[dict[str, object]] = []
    for row in snapshot_samples:
        active_nodes = parse_int(row["active_nodes"])
        avg_queue_len = parse_float(row["avg_queue_len"])
        rows.append(
            {
                "window_start": parse_float(row["t"]),
                "avg_queue_len": avg_queue_len,
                "max_queue_len": parse_int(row["max_queue_len"]),
                "active_nodes": active_nodes,
                "avg_queue_len_per_active_node": round(
                    avg_queue_len / active_nodes if active_nodes else 0.0, 3
                ),
            }
        )

    write_csv(output_dir / "queue_length_over_time.csv", rows)

    time_weighted_rows, summary = compute_time_weighted_queue_stats(queue_samples)
    write_csv(output_dir / "queue_length_by_node.csv", time_weighted_rows)

    if rows:
        xs = [row["window_start"] for row in rows]
        plt.figure(figsize=(9, 5))
        plt.plot(xs, [row["avg_queue_len"] for row in rows], label="avg queue length")
        plt.plot(
            xs,
            [row["avg_queue_len_per_active_node"] for row in rows],
            label="queue per active node",
        )
        plt.xlabel("Simulation time")
        plt.ylabel("Queue length")
        plt.title("Dependency Queue Length Over Time")
        plt.legend()
        plt.tight_layout()
        plt.savefig(output_dir / "queue_length_over_time.png", dpi=150)
        plt.close()

    summary["avg_queue_len_per_active_node"] = round(
        safe_mean([float(row["avg_queue_len_per_active_node"]) for row in rows]), 3
    )
    return summary


def analyze_clock_state(
    snapshot_samples: list[dict[str, str]],
    output_dir: Path,
) -> dict[str, object]:
    rows: list[dict[str, object]] = []
    for row in snapshot_samples:
        active_nodes = parse_int(row["active_nodes"])
        avg_state_size = parse_float(row["avg_state_size"])
        avg_state_bytes = parse_float(row["avg_state_bytes"])
        rows.append(
            {
                "window_start": parse_float(row["t"]),
                "avg_state_size": avg_state_size,
                "max_state_size": parse_int(row["max_state_size"]),
                "avg_state_bytes": avg_state_bytes,
                "max_state_bytes": parse_int(row["max_state_bytes"]),
                "avg_stale_state_entries": parse_float(row["avg_stale_state_entries"]),
                "avg_stale_state_fraction": parse_float(row["avg_stale_state_fraction"]),
                "active_nodes": active_nodes,
                "avg_state_size_per_active_node": round(
                    avg_state_size / active_nodes if active_nodes else 0.0, 3
                ),
                "avg_state_bytes_per_active_node": round(
                    avg_state_bytes / active_nodes if active_nodes else 0.0, 3
                ),
            }
        )

    write_csv(output_dir / "clock_state_over_time.csv", rows)

    if rows:
        xs = [row["window_start"] for row in rows]
        plt.figure(figsize=(9, 5))
        plt.plot(xs, [row["avg_state_size"] for row in rows], label="avg state size")
        plt.plot(
            xs,
            [row["avg_state_bytes_per_active_node"] for row in rows],
            label="state bytes per active node",
        )
        plt.plot(
            xs,
            [row["avg_stale_state_entries"] for row in rows],
            label="stale state entries",
        )
        plt.xlabel("Simulation time")
        plt.ylabel("Clock state")
        plt.title("Clock State Growth Under Churn")
        plt.legend()
        plt.tight_layout()
        plt.savefig(output_dir / "clock_state_over_time.png", dpi=150)
        plt.close()

    return {
        "avg_state_size": round(safe_mean([parse_float(row["avg_state_size"]) for row in snapshot_samples]), 3),
        "p95_state_size": round(
            percentile([parse_float(row["avg_state_size"]) for row in snapshot_samples], 0.95),
            3,
        ),
        "avg_state_bytes": round(safe_mean([parse_float(row["avg_state_bytes"]) for row in snapshot_samples]), 3),
        "p95_state_bytes": round(
            percentile([parse_float(row["avg_state_bytes"]) for row in snapshot_samples], 0.95),
            3,
        ),
        "avg_stale_state_entries": round(
            safe_mean([parse_float(row["avg_stale_state_entries"]) for row in snapshot_samples]),
            3,
        ),
        "avg_stale_state_fraction": round(
            safe_mean([parse_float(row["avg_stale_state_fraction"]) for row in snapshot_samples]),
            3,
        ),
        "avg_state_bytes_per_active_node": round(
            safe_mean([float(row["avg_state_bytes_per_active_node"]) for row in rows]),
            3,
        ),
    }


def analyze_violations(
    violations: list[dict[str, str]],
    deliveries: list[dict[str, str]],
    window: float,
    output_dir: Path,
) -> dict[str, object]:
    rows: list[dict[str, object]] = []
    violation_buckets = bucket_rows(violations, window)
    delivery_buckets = bucket_rows(deliveries, window)
    all_buckets = sorted(set(violation_buckets) | set(delivery_buckets))

    for bucket in all_buckets:
        violation_count = len(violation_buckets.get(bucket, []))
        delivery_count = len(delivery_buckets.get(bucket, []))
        rows.append(
            {
                "window_start": bucket * window,
                "window_end": (bucket + 1) * window,
                "violation_count": violation_count,
                "delivery_count": delivery_count,
                "violation_rate": round(
                    violation_count / delivery_count if delivery_count else 0.0, 4
                ),
            }
        )

    write_csv(output_dir / "violations_over_time.csv", rows)

    if rows:
        xs = [row["window_start"] for row in rows]
        plt.figure(figsize=(9, 5))
        plt.plot(xs, [row["violation_rate"] for row in rows], label="violation rate")
        plt.plot(xs, [row["violation_count"] for row in rows], label="violations / window")
        plt.xlabel("Simulation time")
        plt.ylabel("Violations")
        plt.title("Causal Violations Over Time")
        plt.legend()
        plt.tight_layout()
        plt.savefig(output_dir / "violations_over_time.png", dpi=150)
        plt.close()

    total_deliveries = len(deliveries)
    return {
        "total_violations": len(violations),
        "violation_rate": round(len(violations) / total_deliveries, 4)
        if total_deliveries
        else 0.0,
        "peak_window_violation_rate": round(
            max((float(row["violation_rate"]) for row in rows), default=0.0), 4
        ),
    }


def analyze_latency(
    deliveries: list[dict[str, str]],
    output_dir: Path,
) -> dict[str, object]:
    latencies = [parse_float(row["latency"]) for row in deliveries]
    stats = {
        "avg_latency": round(safe_mean(latencies), 3),
        "latency_p50": round(percentile(latencies, 0.50), 3),
        "latency_p95": round(percentile(latencies, 0.95), 3),
        "latency_p99": round(percentile(latencies, 0.99), 3),
        "max_latency": round(max(latencies, default=0.0), 3),
    }
    write_csv(output_dir / "latency_summary.csv", [stats])

    if latencies:
        plt.figure(figsize=(9, 5))
        plt.hist(latencies, bins=30, edgecolor="black")
        plt.xlabel("Latency")
        plt.ylabel("Count")
        plt.title("Latency Distribution")
        plt.tight_layout()
        plt.savefig(output_dir / "latency_distribution.png", dpi=150)
        plt.close()

    return stats


def analyze_throughput(
    throughput_samples: list[dict[str, str]],
    window: float,
    output_dir: Path,
) -> dict[str, object]:
    rows: list[dict[str, object]] = []
    for bucket, bucket_rows_ in sorted(bucket_rows(throughput_samples, window).items()):
        logical_write_count = sum(
            parse_int(row["count"])
            for row in bucket_rows_
            if row["event_type"] == "logical_write"
        )
        delivery_count = sum(
            parse_int(row["count"])
            for row in bucket_rows_
            if row["event_type"] == "delivery_message"
        )
        rows.append(
            {
                "window_start": bucket * window,
                "window_end": (bucket + 1) * window,
                "logical_write_count": logical_write_count,
                "delivery_message_count": delivery_count,
                "logical_write_throughput": round(logical_write_count / window, 3),
                "delivery_message_throughput": round(delivery_count / window, 3),
            }
        )

    write_csv(output_dir / "throughput_over_time.csv", rows)

    send_rates = [float(row["logical_write_throughput"]) for row in rows]
    delivery_rates = [float(row["delivery_message_throughput"]) for row in rows]
    summary = {
        "avg_logical_write_throughput": round(safe_mean(send_rates), 3),
        "avg_delivery_message_throughput": round(safe_mean(delivery_rates), 3),
        "peak_logical_write_throughput": round(max(send_rates, default=0.0), 3),
        "peak_delivery_message_throughput": round(max(delivery_rates, default=0.0), 3),
    }
    write_csv(output_dir / "throughput_summary.csv", [summary])
    return summary


def build_report_table(sections: dict[str, dict[str, object]], output_dir: Path) -> None:
    rows = []
    for section, metrics in sections.items():
        row = {"section": section}
        row.update(metrics)
        rows.append(row)
    write_csv(output_dir / "report_metrics_table.csv", rows)
    (output_dir / "report_metrics_summary.json").write_text(json.dumps(sections, indent=2))


def analyze_run(
    input_dir: Path,
    run_name: str,
    window: float,
    output_dir: Path,
) -> dict[str, dict[str, object]]:
    output_dir.mkdir(parents=True, exist_ok=True)

    sends = load_csv(input_dir / f"{run_name}_sends.csv")
    deliveries = load_csv(input_dir / f"{run_name}_deliveries.csv")
    joins = load_csv(input_dir / f"{run_name}_joins.csv")
    leaves = load_csv(input_dir / f"{run_name}_leaves.csv")
    queue_samples = load_csv(input_dir / f"{run_name}_queue_samples.csv")
    snapshot_samples = load_csv(input_dir / f"{run_name}_snapshot_samples.csv")
    throughput_samples = load_csv(input_dir / f"{run_name}_throughput_samples.csv")
    violations = load_csv(input_dir / f"{run_name}_violations.csv")

    sections = {
        "churn": analyze_churn(joins, leaves, snapshot_samples, window, output_dir),
        "metadata_growth": analyze_metadata_growth(sends, window, output_dir),
        "metadata_representation": analyze_metadata_representation(
            sends, snapshot_samples, window, output_dir
        ),
        "stale_metadata": analyze_stale_metadata(sends, window, output_dir),
        "queue_length": analyze_queue_lengths(queue_samples, snapshot_samples, output_dir),
        "clock_state": analyze_clock_state(snapshot_samples, output_dir),
        "violations": analyze_violations(violations, deliveries, window, output_dir),
        "latency": analyze_latency(deliveries, output_dir),
        "throughput": analyze_throughput(throughput_samples, window, output_dir),
    }
    build_report_table(sections, output_dir)
    return sections


def build_parser() -> configargparse.ArgParser:
    parser = configargparse.ArgParser(
        description="Analyze a simulator run.",
        default_config_files=["configs/analyze.yaml"],
        config_file_parser_class=configargparse.YAMLConfigFileParser,
    )
    parser.add(
        "-c",
        "--config",
        is_config_file=True,
        help="Path to a YAML config file.",
    )
    parser.add_argument("--input-dir", default="output/runs")
    parser.add_argument("--run-name", default="run")
    parser.add_argument("--window", type=float, default=25.0)
    parser.add_argument("--output-dir", default=None)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    input_dir = Path(args.input_dir)
    output_dir = Path(args.output_dir) if args.output_dir else input_dir / f"{args.run_name}_analysis"
    analyze_run(input_dir, args.run_name, args.window, output_dir)


if __name__ == "__main__":
    main()
