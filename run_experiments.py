from __future__ import annotations

import csv
import json
import os
from pathlib import Path
from typing import Any

import configargparse

os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib")
os.environ.setdefault("XDG_CACHE_HOME", "/tmp/.cache")

import matplotlib.pyplot as plt

from analyze_run import analyze_run
from code import CLOCK_FACTORIES, run_scenario, save_run


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


def flatten_sections(sections: dict[str, dict[str, object]]) -> dict[str, object]:
    flat: dict[str, object] = {}
    for section, metrics in sections.items():
        for key, value in metrics.items():
            flat[f"{section}.{key}"] = value
    return flat


def mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def aggregate_runs(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    grouped: dict[tuple[str, str], list[dict[str, object]]] = {}
    for row in rows:
        key = (str(row["profile"]), str(row["clock"]))
        grouped.setdefault(key, []).append(row)

    aggregated: list[dict[str, object]] = []
    metric_keys = [
        key
        for key in rows[0].keys()
        if key not in {"profile", "clock", "seed", "run_dir", "analysis_dir", "run_name"}
    ]
    for (profile, clock), group in sorted(grouped.items()):
        row: dict[str, object] = {
            "profile": profile,
            "clock": clock,
            "num_runs": len(group),
        }
        for metric in metric_keys:
            numeric_values = [
                float(candidate[metric])
                for candidate in group
                if isinstance(candidate.get(metric), (int, float))
            ]
            if numeric_values:
                row[metric] = round(mean(numeric_values), 3)
        aggregated.append(row)
    return aggregated


def make_comparison_plots(rows: list[dict[str, object]], output_dir: Path) -> None:
    metrics = [
        ("metadata_growth.avg_metadata_size", "Average Metadata Size", "metadata_comparison.png"),
        (
            "stale_metadata.avg_stale_metadata_entries",
            "Average Stale Metadata Entries",
            "stale_metadata_comparison.png",
        ),
        (
            "clock_state.avg_state_size",
            "Average Clock State Size",
            "clock_state_comparison.png",
        ),
        ("latency.latency_p95", "P95 Latency", "latency_p95_comparison.png"),
        (
            "queue_length.avg_time_weighted_queue_len",
            "Avg Time-Weighted Queue Length",
            "queue_comparison.png",
        ),
        (
            "throughput.avg_logical_write_throughput",
            "Average Logical Write Throughput",
            "logical_write_throughput_comparison.png",
        ),
    ]
    profiles = sorted({str(row["profile"]) for row in rows})
    clocks = sorted({str(row["clock"]) for row in rows})

    for metric_key, title, filename in metrics:
        plt.figure(figsize=(10, 5))
        x_labels: list[str] = []
        values: list[float] = []
        for profile in profiles:
            for clock in clocks:
                match = next(
                    (
                        row
                        for row in rows
                        if str(row["profile"]) == profile and str(row["clock"]) == clock
                    ),
                    None,
                )
                if match is None or metric_key not in match:
                    continue
                x_labels.append(f"{profile}\n{clock}")
                values.append(float(match[metric_key]))

        if not values:
            plt.close()
            continue

        plt.bar(range(len(values)), values)
        plt.xticks(range(len(values)), x_labels)
        plt.ylabel(title)
        plt.title(title)
        plt.tight_layout()
        plt.savefig(output_dir / filename, dpi=150)
        plt.close()


def build_parser() -> configargparse.ArgParser:
    parser = configargparse.ArgParser(
        description="Run a full repeatable experiment matrix.",
        default_config_files=["configs/full_experiment.yaml"],
        config_file_parser_class=configargparse.YAMLConfigFileParser,
    )
    parser.add(
        "-c",
        "--config",
        is_config_file=True,
        help="Path to a YAML config file.",
    )
    parser.add("--clocks", nargs="+", choices=sorted(CLOCK_FACTORIES), default=["vector", "dvv"])
    parser.add("--profiles", nargs="+", choices=["stable", "low", "sustained", "burst"], default=["stable", "sustained"])
    parser.add("--seeds", nargs="+", type=int, default=[11, 22, 33])
    parser.add("--sim-time", type=float, default=300.0)
    parser.add("--initial-size", type=int, default=15)
    parser.add("--write-interval", type=float, default=20.0)
    parser.add("--max-nodes", type=int, default=40)
    parser.add("--min-nodes", type=int, default=5)
    parser.add("--min-lat", type=float, default=1.0)
    parser.add("--max-lat", type=float, default=5.0)
    parser.add("--key-count", type=int, default=5)
    parser.add("--sample-interval", type=float, default=20.0)
    parser.add("--analysis-window", type=float, default=25.0)
    parser.add("--output-dir", default="output/experiments")
    parser.add("--experiment-name", default="baseline")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    experiment_dir = Path(args.output_dir) / args.experiment_name
    experiment_dir.mkdir(parents=True, exist_ok=True)

    experiment_config = {
        "clocks": args.clocks,
        "profiles": args.profiles,
        "seeds": args.seeds,
        "sim_time": args.sim_time,
        "initial_size": args.initial_size,
        "write_interval": args.write_interval,
        "max_nodes": args.max_nodes,
        "min_nodes": args.min_nodes,
        "min_lat": args.min_lat,
        "max_lat": args.max_lat,
        "key_count": args.key_count,
        "sample_interval": args.sample_interval,
        "analysis_window": args.analysis_window,
        "output_dir": args.output_dir,
        "experiment_name": args.experiment_name,
    }
    (experiment_dir / "experiment_config.json").write_text(json.dumps(experiment_config, indent=2))

    run_rows: list[dict[str, object]] = []
    for profile in args.profiles:
        for clock in args.clocks:
            for seed in args.seeds:
                run_name = f"{profile}_{clock}_seed{seed}"
                run_dir = experiment_dir / run_name
                metrics = run_scenario(
                    profile=profile,
                    clock_factory=CLOCK_FACTORIES[clock],
                    sim_time=args.sim_time,
                    seed=seed,
                    initial_size=args.initial_size,
                    write_interval=args.write_interval,
                    max_nodes=args.max_nodes,
                    min_nodes=args.min_nodes,
                    min_lat=args.min_lat,
                    max_lat=args.max_lat,
                    key_count=args.key_count,
                    sample_interval=args.sample_interval,
                )
                run_config = {
                    "profile": profile,
                    "clock": clock,
                    "seed": seed,
                    "sim_time": args.sim_time,
                    "initial_size": args.initial_size,
                    "write_interval": args.write_interval,
                    "max_nodes": args.max_nodes,
                    "min_nodes": args.min_nodes,
                    "min_lat": args.min_lat,
                    "max_lat": args.max_lat,
                    "key_count": args.key_count,
                    "sample_interval": args.sample_interval,
                    "output_dir": str(run_dir),
                    "run_name": run_name,
                }
                save_run(
                    metrics,
                    output_dir=run_dir,
                    run_name=run_name,
                    config=run_config,
                    sim_time=args.sim_time,
                )
                analysis_dir = run_dir / f"{run_name}_analysis"
                sections = analyze_run(
                    input_dir=run_dir,
                    run_name=run_name,
                    window=args.analysis_window,
                    output_dir=analysis_dir,
                )
                row = {
                    "profile": profile,
                    "clock": clock,
                    "seed": seed,
                    "run_name": run_name,
                    "run_dir": str(run_dir),
                    "analysis_dir": str(analysis_dir),
                }
                row.update(flatten_sections(sections))
                run_rows.append(row)

    write_csv(experiment_dir / "comparison_runs.csv", run_rows)
    aggregated_rows = aggregate_runs(run_rows)
    write_csv(experiment_dir / "comparison_by_clock.csv", aggregated_rows)
    (experiment_dir / "comparison_by_clock.json").write_text(json.dumps(aggregated_rows, indent=2))
    make_comparison_plots(aggregated_rows, experiment_dir)


if __name__ == "__main__":
    main()
