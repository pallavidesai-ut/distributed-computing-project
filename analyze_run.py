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
        for key in row:
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


def line_plot(
    rows: list[dict[str, object]],
    *,
    x_key: str,
    y_specs: list[tuple[str, str]],
    title: str,
    ylabel: str,
    output_path: Path,
) -> None:
    if not rows:
        return
    xs = [float(row[x_key]) for row in rows]
    plt.figure(figsize=(9, 5))
    for key, label in y_specs:
        plt.plot(xs, [float(row[key]) for row in rows], label=label)
    plt.xlabel("Simulation time")
    plt.ylabel(ylabel)
    plt.title(title)
    plt.legend()
    plt.tight_layout()
    plt.savefig(output_path, dpi=150)
    plt.close()


def analyze_churn(
    joins: list[dict[str, str]],
    leaves: list[dict[str, str]],
    snapshots: list[dict[str, str]],
    window: float,
    output_dir: Path,
) -> dict[str, object]:
    join_buckets = bucket_rows(joins, window)
    leave_buckets = bucket_rows(leaves, window)
    snapshot_buckets = bucket_rows(snapshots, window)
    rows: list[dict[str, object]] = []
    for bucket in sorted(set(join_buckets) | set(leave_buckets) | set(snapshot_buckets)):
        join_count = len(join_buckets.get(bucket, []))
        leave_count = len(leave_buckets.get(bucket, []))
        samples = snapshot_buckets.get(bucket, [])
        avg_active_nodes = safe_mean([parse_int(row["active_nodes"]) for row in samples])
        rows.append(
            {
                "window_start": bucket * window,
                "join_count": join_count,
                "leave_count": leave_count,
                "total_churn": join_count + leave_count,
                "avg_active_nodes": round(avg_active_nodes, 3),
            }
        )
    write_csv(output_dir / "churn_over_time.csv", rows)
    line_plot(
        rows,
        x_key="window_start",
        y_specs=[("avg_active_nodes", "active nodes"), ("total_churn", "churn events")],
        title="Replica Churn Over Time",
        ylabel="Cluster activity",
        output_path=output_dir / "churn_over_time.png",
    )
    return {
        "total_joins": len(joins),
        "total_leaves": len(leaves),
        "avg_active_nodes": round(safe_mean([parse_int(row["active_nodes"]) for row in snapshots]), 3),
        "peak_active_nodes": max((parse_int(row["active_nodes"]) for row in snapshots), default=0),
    }


def analyze_metadata(
    writes: list[dict[str, str]],
    window: float,
    output_dir: Path,
) -> dict[str, object]:
    rows: list[dict[str, object]] = []
    for bucket, bucket_rows_ in sorted(bucket_rows(writes, window).items()):
        metadata_bytes = [parse_float(row["metadata_bytes"]) for row in bucket_rows_]
        components = [parse_float(row["metadata_components"]) for row in bucket_rows_]
        actor_entries = [parse_float(row["actor_entries"]) for row in bucket_rows_]
        pruned = [parse_int(row["was_pruned"]) for row in bucket_rows_]
        rows.append(
            {
                "window_start": bucket * window,
                "avg_metadata_bytes": round(safe_mean(metadata_bytes), 3),
                "p95_metadata_bytes": round(percentile(metadata_bytes, 0.95), 3),
                "avg_metadata_components": round(safe_mean(components), 3),
                "avg_actor_entries": round(safe_mean(actor_entries), 3),
                "pruned_write_rate": round(safe_mean(pruned), 4),
            }
        )
    write_csv(output_dir / "metadata_over_time.csv", rows)
    line_plot(
        rows,
        x_key="window_start",
        y_specs=[
            ("avg_metadata_bytes", "avg bytes"),
            ("avg_actor_entries", "avg actor entries"),
        ],
        title="Version Metadata Cost Over Time",
        ylabel="Metadata",
        output_path=output_dir / "metadata_over_time.png",
    )
    metadata_bytes = [parse_float(row["metadata_bytes"]) for row in writes]
    actor_entries = [parse_float(row["actor_entries"]) for row in writes]
    pruned = [parse_int(row["was_pruned"]) for row in writes]
    return {
        "avg_metadata_bytes": round(safe_mean(metadata_bytes), 3),
        "p95_metadata_bytes": round(percentile(metadata_bytes, 0.95), 3),
        "avg_actor_entries": round(safe_mean(actor_entries), 3),
        "pruned_write_rate": round(safe_mean(pruned), 4),
    }


def analyze_accuracy(
    accuracy: list[dict[str, str]],
    window: float,
    output_dir: Path,
) -> dict[str, object]:
    rows: list[dict[str, object]] = []
    for bucket, bucket_rows_ in sorted(bucket_rows(accuracy, window).items()):
        precision = [parse_float(row["precision"]) for row in bucket_rows_]
        recall = [parse_float(row["recall"]) for row in bucket_rows_]
        false_positive = [parse_float(row["false_positive_events"]) for row in bucket_rows_]
        false_negative = [parse_float(row["false_negative_events"]) for row in bucket_rows_]
        rows.append(
            {
                "window_start": bucket * window,
                "avg_precision": round(safe_mean(precision), 4),
                "avg_recall": round(safe_mean(recall), 4),
                "avg_false_positive_events": round(safe_mean(false_positive), 3),
                "avg_false_negative_events": round(safe_mean(false_negative), 3),
            }
        )
    write_csv(output_dir / "accuracy_over_time.csv", rows)
    line_plot(
        rows,
        x_key="window_start",
        y_specs=[("avg_precision", "precision"), ("avg_recall", "recall")],
        title="Clock History Fidelity Over Time",
        ylabel="Score",
        output_path=output_dir / "accuracy_over_time.png",
    )
    precision = [parse_float(row["precision"]) for row in accuracy]
    recall = [parse_float(row["recall"]) for row in accuracy]
    false_positive = [parse_float(row["false_positive_events"]) for row in accuracy]
    false_negative = [parse_float(row["false_negative_events"]) for row in accuracy]
    return {
        "avg_precision": round(safe_mean(precision), 4),
        "avg_recall": round(safe_mean(recall), 4),
        "avg_false_positive_events": round(safe_mean(false_positive), 3),
        "avg_false_negative_events": round(safe_mean(false_negative), 3),
    }


def analyze_decision_quality(
    decisions: list[dict[str, str]],
    window: float,
    output_dir: Path,
) -> dict[str, object]:
    rows: list[dict[str, object]] = []
    for bucket, bucket_rows_ in sorted(bucket_rows(decisions, window).items()):
        concurrent_pairs = [
            row for row in bucket_rows_ if row["true_relation"] == "concurrent"
        ]
        descendant_pairs = [
            row
            for row in bucket_rows_
            if row["true_relation"] in {"dominates", "dominated"}
        ]
        missed_conflicts = [
            row
            for row in concurrent_pairs
            if row["clock_relation"] in {"dominates", "dominated", "equal"}
            and row["final_action"] in {"drop_existing", "drop_incoming"}
        ]
        stale_siblings = [
            row for row in descendant_pairs if row["final_action"] == "keep_both"
        ]
        rows.append(
            {
                "window_start": bucket * window,
                "missed_conflict_rate": round(
                    len(missed_conflicts) / len(concurrent_pairs) if concurrent_pairs else 0.0,
                    4,
                ),
                "stale_sibling_rate": round(
                    len(stale_siblings) / len(descendant_pairs) if descendant_pairs else 0.0,
                    4,
                ),
                "concurrent_pair_count": len(concurrent_pairs),
            }
        )
    write_csv(output_dir / "decision_quality_over_time.csv", rows)
    line_plot(
        rows,
        x_key="window_start",
        y_specs=[
            ("missed_conflict_rate", "missed conflict rate"),
            ("stale_sibling_rate", "stale sibling rate"),
        ],
        title="Version Resolution Errors Over Time",
        ylabel="Error rate",
        output_path=output_dir / "decision_quality_over_time.png",
    )

    concurrent_pairs = [row for row in decisions if row["true_relation"] == "concurrent"]
    descendant_pairs = [
        row for row in decisions if row["true_relation"] in {"dominates", "dominated"}
    ]
    missed_conflicts = [
        row
        for row in concurrent_pairs
        if row["clock_relation"] in {"dominates", "dominated", "equal"}
        and row["final_action"] in {"drop_existing", "drop_incoming"}
    ]
    stale_siblings = [
        row for row in descendant_pairs if row["final_action"] == "keep_both"
    ]
    return {
        "missed_conflict_rate": round(
            len(missed_conflicts) / len(concurrent_pairs) if concurrent_pairs else 0.0,
            4,
        ),
        "stale_sibling_rate": round(
            len(stale_siblings) / len(descendant_pairs) if descendant_pairs else 0.0,
            4,
        ),
        "concurrent_pair_count": len(concurrent_pairs),
    }


def analyze_replica_state(
    snapshots: list[dict[str, str]],
    output_dir: Path,
) -> dict[str, object]:
    rows: list[dict[str, object]] = []
    for row in snapshots:
        rows.append(
            {
                "window_start": parse_float(row["t"]),
                "avg_versions_per_key": parse_float(row["avg_versions_per_key"]),
                "avg_hot_key_siblings": parse_float(row["avg_hot_key_siblings"]),
                "max_hot_key_siblings": parse_int(row["max_hot_key_siblings"]),
                "avg_metadata_bytes": parse_float(row["avg_metadata_bytes"]),
                "avg_sibling_set_metadata_bytes": parse_float(
                    row.get("avg_sibling_set_metadata_bytes", row["avg_metadata_bytes"])
                ),
                "avg_sibling_set_metadata_components": parse_float(
                    row.get("avg_sibling_set_metadata_components", "0")
                ),
                "avg_stale_actor_fraction": parse_float(row["avg_stale_actor_fraction"]),
            }
        )
    write_csv(output_dir / "replica_state_over_time.csv", rows)
    line_plot(
        rows,
        x_key="window_start",
        y_specs=[
            ("avg_hot_key_siblings", "avg hot-key siblings"),
            ("avg_stale_actor_fraction", "stale replica-actor fraction"),
        ],
        title="Replica-State Pressure Over Time",
        ylabel="State",
        output_path=output_dir / "replica_state_over_time.png",
    )
    return {
        "avg_versions_per_key": round(
            safe_mean([parse_float(row["avg_versions_per_key"]) for row in snapshots]),
            3,
        ),
        "avg_hot_key_siblings": round(
            safe_mean([parse_float(row["avg_hot_key_siblings"]) for row in snapshots]),
            3,
        ),
        "p95_hot_key_siblings": round(
            percentile([parse_float(row["avg_hot_key_siblings"]) for row in snapshots], 0.95),
            3,
        ),
        "avg_stale_actor_fraction": round(
            safe_mean([parse_float(row["avg_stale_actor_fraction"]) for row in snapshots]),
            4,
        ),
        "avg_sibling_set_metadata_bytes": round(
            safe_mean(
                [
                    parse_float(
                        row.get(
                            "avg_sibling_set_metadata_bytes",
                            row["avg_metadata_bytes"],
                        )
                    )
                    for row in snapshots
                ]
            ),
            3,
        ),
        "avg_sibling_set_metadata_components": round(
            safe_mean(
                [
                    parse_float(row.get("avg_sibling_set_metadata_components", "0"))
                    for row in snapshots
                ]
            ),
            3,
        ),
    }


def analyze_latency_with_prefix(
    rows: list[dict[str, str]],
    output_dir: Path,
    *,
    prefix: str,
    title: str,
    x_label: str,
) -> dict[str, object]:
    latencies = [parse_float(row["latency"]) for row in rows]
    stats = {
        "avg_latency": round(safe_mean(latencies), 3),
        "median_latency": round(percentile(latencies, 0.50), 3),
        "latency_p95": round(percentile(latencies, 0.95), 3),
        "latency_p99": round(percentile(latencies, 0.99), 3),
        "max_latency": round(max(latencies, default=0.0), 3),
    }
    output_prefix = output_dir / f"{prefix}_"
    write_csv(output_prefix.with_name(f"{output_prefix.name}summary.csv"), [stats])
    if not rows:
        return stats
    write_csv(output_dir / f"{prefix}_latency_details.csv", rows)
    if latencies:
        plt.figure(figsize=(9, 5))
        plt.hist(latencies, bins=30, edgecolor="black")
        plt.xlabel(f"{x_label} (sim time units)")
        plt.ylabel("Count")
        plt.title(title)
        plt.tight_layout()
        plt.savefig(output_prefix.with_name(f"{output_prefix.name}distribution.png"), dpi=150)
        plt.close()
    return stats


def analyze_replication_latency(deliveries: list[dict[str, str]], output_dir: Path) -> dict[str, object]:
    return analyze_latency_with_prefix(
        deliveries,
        output_dir=output_dir,
        prefix="replication",
        title="Replication Latency Distribution",
        x_label="Replication latency",
    )


def analyze_client_latency(
    client_latencies: list[dict[str, str]],
    output_dir: Path,
) -> dict[str, object]:
    return analyze_latency_with_prefix(
        client_latencies,
        output_dir=output_dir,
        prefix="client_write",
        title="Client-Visible Write Latency Distribution",
        x_label="Client-visible write latency",
    )


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
    latency_output_dir = output_dir / "latency"
    latency_output_dir.mkdir(parents=True, exist_ok=True)
    writes = load_csv(input_dir / f"{run_name}_writes.csv")
    deliveries = load_csv(input_dir / f"{run_name}_deliveries.csv")
    decisions = load_csv(input_dir / f"{run_name}_decisions.csv")
    snapshots = load_csv(input_dir / f"{run_name}_snapshots.csv")
    joins = load_csv(input_dir / f"{run_name}_joins.csv")
    leaves = load_csv(input_dir / f"{run_name}_leaves.csv")
    accuracy = load_csv(input_dir / f"{run_name}_accuracy.csv")
    client_latencies = load_csv(input_dir / f"{run_name}_client_latencies.csv")

    sections = {
        "churn": analyze_churn(joins, leaves, snapshots, window, output_dir),
        "metadata": analyze_metadata(writes, window, output_dir),
        "accuracy": analyze_accuracy(accuracy, window, output_dir),
        "decision_quality": analyze_decision_quality(decisions, window, output_dir),
        "replica_state": analyze_replica_state(snapshots, output_dir),
        "replication_latency": analyze_replication_latency(deliveries, latency_output_dir),
        "client_latency": analyze_client_latency(client_latencies, latency_output_dir),
    }
    build_report_table(sections, output_dir)
    return sections


def build_parser() -> configargparse.ArgParser:
    parser = configargparse.ArgParser(
        description="Analyze a causality-simulation run.",
        default_config_files=["configs/analyze.yaml"],
        config_file_parser_class=configargparse.YAMLConfigFileParser,
    )
    parser.add("-c", "--config", is_config_file=True, help="Path to a YAML config file.")
    parser.add_argument("--input-dir", default="output/runs")
    parser.add_argument("--run-name", default="clock_study")
    parser.add_argument("--window", type=float, default=12.0)
    parser.add_argument("--output-dir", default=None)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    input_dir = Path(args.input_dir)
    output_dir = Path(args.output_dir) if args.output_dir else input_dir / f"{args.run_name}_analysis"
    analyze_run(input_dir, args.run_name, args.window, output_dir)


if __name__ == "__main__":
    main()
