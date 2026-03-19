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
        "avg_time_weighted_queue_len": round(mean(weighted_averages), 3)
        if weighted_averages
        else 0.0,
        "p95_time_weighted_queue_len": round(percentile(weighted_averages, 0.95), 3),
        "max_queue_len": max(max_values, default=0),
        "avg_fraction_time_non_empty": round(mean(non_empty_fractions), 3)
        if non_empty_fractions
        else 0.0,
    }
    return node_rows, summary


def analyze_metadata_growth(
    sends: list[dict[str, str]],
    window: float,
    output_dir: Path,
) -> dict[str, object]:
    rows: list[dict[str, object]] = []
    buckets = bucket_rows(sends, window)
    for bucket in sorted(buckets):
        bucket_rows_ = buckets[bucket]
        meta_sizes = [parse_int(row["meta_size"]) for row in bucket_rows_]
        rows.append(
            {
                "window_start": bucket * window,
                "window_end": (bucket + 1) * window,
                "send_count": len(bucket_rows_),
                "avg_metadata_size": round(mean(meta_sizes), 3),
                "p95_metadata_size": round(percentile(meta_sizes, 0.95), 3),
                "max_metadata_size": max(meta_sizes),
            }
        )

    write_csv(output_dir / "metadata_growth.csv", rows)

    if rows:
        xs = [row["window_start"] for row in rows]
        avg_meta = [row["avg_metadata_size"] for row in rows]
        p95_meta = [row["p95_metadata_size"] for row in rows]
        max_meta = [row["max_metadata_size"] for row in rows]

        plt.figure(figsize=(9, 5))
        plt.plot(xs, avg_meta, label="avg")
        plt.plot(xs, p95_meta, label="p95")
        plt.plot(xs, max_meta, label="max")
        plt.xlabel("Simulation time")
        plt.ylabel("Metadata size")
        plt.title("Metadata Growth Over Time")
        plt.legend()
        plt.tight_layout()
        plt.savefig(output_dir / "metadata_growth.png", dpi=150)
        plt.close()

    all_meta_sizes = [parse_int(row["meta_size"]) for row in sends]
    return {
        "avg_metadata_size": round(mean(all_meta_sizes), 3) if all_meta_sizes else 0.0,
        "p95_metadata_size": round(percentile(all_meta_sizes, 0.95), 3),
        "max_metadata_size": max(all_meta_sizes, default=0),
    }


def analyze_stale_metadata(
    sends: list[dict[str, str]],
    window: float,
    output_dir: Path,
) -> dict[str, object]:
    rows: list[dict[str, object]] = []
    buckets = bucket_rows(sends, window)
    for bucket in sorted(buckets):
        bucket_rows_ = buckets[bucket]
        stale_entries = [parse_int(row["stale_metadata_entries"]) for row in bucket_rows_]
        stale_fractions = [parse_float(row["stale_metadata_fraction"]) for row in bucket_rows_]
        rows.append(
            {
                "window_start": bucket * window,
                "window_end": (bucket + 1) * window,
                "send_count": len(bucket_rows_),
                "avg_stale_metadata_entries": round(mean(stale_entries), 3),
                "p95_stale_metadata_entries": round(percentile(stale_entries, 0.95), 3),
                "avg_stale_metadata_fraction": round(mean(stale_fractions), 3),
            }
        )

    write_csv(output_dir / "stale_metadata_over_time.csv", rows)

    if rows:
        xs = [row["window_start"] for row in rows]
        stale_entries = [row["avg_stale_metadata_entries"] for row in rows]
        stale_fraction = [row["avg_stale_metadata_fraction"] for row in rows]

        plt.figure(figsize=(9, 5))
        plt.plot(xs, stale_entries, label="avg stale entries")
        plt.plot(xs, stale_fraction, label="avg stale fraction")
        plt.xlabel("Simulation time")
        plt.ylabel("Stale membership metadata")
        plt.title("Stale Metadata Under Churn")
        plt.legend()
        plt.tight_layout()
        plt.savefig(output_dir / "stale_metadata_over_time.png", dpi=150)
        plt.close()

    all_stale_entries = [parse_int(row["stale_metadata_entries"]) for row in sends]
    all_stale_fractions = [parse_float(row["stale_metadata_fraction"]) for row in sends]
    return {
        "avg_stale_metadata_entries": round(mean(all_stale_entries), 3)
        if all_stale_entries
        else 0.0,
        "p95_stale_metadata_entries": round(percentile(all_stale_entries, 0.95), 3),
        "avg_stale_metadata_fraction": round(mean(all_stale_fractions), 3)
        if all_stale_fractions
        else 0.0,
    }


def analyze_queue_lengths(
    queue_samples: list[dict[str, str]],
    snapshot_samples: list[dict[str, str]],
    window: float,
    output_dir: Path,
) -> dict[str, object]:
    rows: list[dict[str, object]] = []
    for row in snapshot_samples:
        rows.append(
            {
                "window_start": parse_float(row["t"]),
                "avg_queue_len": parse_float(row["avg_queue_len"]),
                "max_queue_len": parse_int(row["max_queue_len"]),
                "active_nodes": parse_int(row["active_nodes"]),
            }
        )

    write_csv(output_dir / "queue_length_over_time.csv", rows)

    time_weighted_rows, time_weighted_summary = compute_time_weighted_queue_stats(
        queue_samples
    )
    write_csv(output_dir / "queue_length_by_node.csv", time_weighted_rows)

    if rows:
        xs = [row["window_start"] for row in rows]
        avg_queue = [row["avg_queue_len"] for row in rows]
        max_queue = [row["max_queue_len"] for row in rows]

        plt.figure(figsize=(9, 5))
        plt.plot(xs, avg_queue, label="avg")
        plt.plot(xs, max_queue, label="max")
        plt.xlabel("Simulation time")
        plt.ylabel("Queue length")
        plt.title("Dependency Queue Length Over Time")
        plt.legend()
        plt.tight_layout()
        plt.savefig(output_dir / "queue_length_over_time.png", dpi=150)
        plt.close()

    return time_weighted_summary


def analyze_clock_state(
    snapshot_samples: list[dict[str, str]],
    window: float,
    output_dir: Path,
) -> dict[str, object]:
    rows: list[dict[str, object]] = []
    for row in snapshot_samples:
        rows.append(
            {
                "window_start": parse_float(row["t"]),
                "avg_state_size": parse_float(row["avg_state_size"]),
                "max_state_size": parse_int(row["max_state_size"]),
                "avg_stale_state_entries": parse_float(row["avg_stale_state_entries"]),
                "avg_stale_state_fraction": parse_float(row["avg_stale_state_fraction"]),
                "active_nodes": parse_int(row["active_nodes"]),
            }
        )

    write_csv(output_dir / "clock_state_over_time.csv", rows)

    if rows:
        xs = [row["window_start"] for row in rows]
        state_size = [row["avg_state_size"] for row in rows]
        stale_entries = [row["avg_stale_state_entries"] for row in rows]

        plt.figure(figsize=(9, 5))
        plt.plot(xs, state_size, label="avg state size")
        plt.plot(xs, stale_entries, label="avg stale state entries")
        plt.xlabel("Simulation time")
        plt.ylabel("Clock state")
        plt.title("Clock State Growth Under Churn")
        plt.legend()
        plt.tight_layout()
        plt.savefig(output_dir / "clock_state_over_time.png", dpi=150)
        plt.close()

    all_state_sizes = [parse_float(row["avg_state_size"]) for row in snapshot_samples]
    all_stale_entries = [parse_float(row["avg_stale_state_entries"]) for row in snapshot_samples]
    all_stale_fractions = [parse_float(row["avg_stale_state_fraction"]) for row in snapshot_samples]
    return {
        "avg_state_size": round(mean(all_state_sizes), 3) if all_state_sizes else 0.0,
        "p95_state_size": round(percentile(all_state_sizes, 0.95), 3),
        "avg_stale_state_entries": round(mean(all_stale_entries), 3)
        if all_stale_entries
        else 0.0,
        "avg_stale_state_fraction": round(mean(all_stale_fractions), 3)
        if all_stale_fractions
        else 0.0,
    }


def analyze_latency(
    deliveries: list[dict[str, str]],
    output_dir: Path,
) -> dict[str, object]:
    latencies = [parse_float(row["latency"]) for row in deliveries]
    stats = {
        "avg_latency": round(mean(latencies), 3) if latencies else 0.0,
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
    buckets = bucket_rows(throughput_samples, window)
    for bucket in sorted(buckets):
        bucket_rows_ = buckets[bucket]
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

    if rows:
        xs = [row["window_start"] for row in rows]
        send_tp = [row["logical_write_throughput"] for row in rows]
        delivery_tp = [row["delivery_message_throughput"] for row in rows]

        plt.figure(figsize=(9, 5))
        plt.plot(xs, send_tp, label="logical write throughput")
        plt.plot(xs, delivery_tp, label="delivery message throughput")
        plt.xlabel("Simulation time")
        plt.ylabel("Events per time unit")
        plt.title("Throughput Over Time")
        plt.legend()
        plt.tight_layout()
        plt.savefig(output_dir / "throughput_over_time.png", dpi=150)
        plt.close()

    send_rates = [row["logical_write_throughput"] for row in rows]
    delivery_rates = [row["delivery_message_throughput"] for row in rows]
    summary = {
        "avg_logical_write_throughput": round(mean(send_rates), 3)
        if send_rates
        else 0.0,
        "avg_delivery_message_throughput": round(mean(delivery_rates), 3)
        if delivery_rates
        else 0.0,
        "peak_logical_write_throughput": round(max(send_rates, default=0.0), 3),
        "peak_delivery_message_throughput": round(
            max(delivery_rates, default=0.0), 3
        ),
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
    queue_samples = load_csv(input_dir / f"{run_name}_queue_samples.csv")
    snapshot_samples = load_csv(input_dir / f"{run_name}_snapshot_samples.csv")
    throughput_samples = load_csv(input_dir / f"{run_name}_throughput_samples.csv")

    sections = {
        "metadata_growth": analyze_metadata_growth(sends, window, output_dir),
        "stale_metadata": analyze_stale_metadata(sends, window, output_dir),
        "queue_length": analyze_queue_lengths(queue_samples, snapshot_samples, window, output_dir),
        "clock_state": analyze_clock_state(snapshot_samples, window, output_dir),
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
