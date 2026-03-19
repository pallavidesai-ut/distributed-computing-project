from __future__ import annotations

import csv
import json
import math
import os
from pathlib import Path

import configargparse

os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib")
os.environ.setdefault("XDG_CACHE_HOME", "/tmp/.cache")

import matplotlib.pyplot as plt

from analyze_run import analyze_run
from code import CLOCK_FACTORIES, make_clock_factory, run_scenario, save_run


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


def load_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="") as handle:
        return list(csv.DictReader(handle))


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
            values = [
                float(candidate[metric])
                for candidate in group
                if isinstance(candidate.get(metric), (int, float))
            ]
            if values:
                row[metric] = round(mean(values), 3)
        aggregated.append(row)
    return aggregated


def aggregate_time_series(
    run_rows: list[dict[str, object]],
    *,
    relative_csv: str,
    x_key: str,
    y_keys: list[str],
) -> list[dict[str, object]]:
    grouped: dict[tuple[str, str, float], dict[str, list[float]]] = {}
    for row in run_rows:
        analysis_dir = Path(str(row["analysis_dir"]))
        for point in load_csv(analysis_dir / relative_csv):
            x_value = float(point[x_key])
            key = (str(row["profile"]), str(row["clock"]), x_value)
            bucket = grouped.setdefault(key, {metric: [] for metric in y_keys})
            for metric in y_keys:
                value = point.get(metric)
                if value not in {None, ""}:
                    bucket[metric].append(float(value))

    aggregated: list[dict[str, object]] = []
    for (profile, clock, x_value), metric_values in sorted(grouped.items()):
        entry: dict[str, object] = {
            "profile": profile,
            "clock": clock,
            x_key: round(x_value, 3),
        }
        for metric, values in metric_values.items():
            if values:
                entry[metric] = round(mean(values), 3)
        aggregated.append(entry)
    return aggregated


def grouped_profile_bar_plot(
    rows: list[dict[str, object]],
    *,
    metric_key: str,
    ylabel: str,
    title: str,
    filename: str,
    output_dir: Path,
) -> None:
    profiles = sorted({str(row["profile"]) for row in rows})
    clocks = sorted({str(row["clock"]) for row in rows})
    if not profiles or not clocks:
        return

    width = 0.8 / max(len(clocks), 1)
    x_positions = list(range(len(profiles)))
    fig, ax = plt.subplots(figsize=(10, 5))
    plotted = False

    for idx, clock in enumerate(clocks):
        values: list[float] = []
        positions: list[float] = []
        for profile_index, profile in enumerate(profiles):
            match = next(
                (
                    row
                    for row in rows
                    if str(row["profile"]) == profile
                    and str(row["clock"]) == clock
                    and metric_key in row
                ),
                None,
            )
            if match is None:
                values.append(math.nan)
            else:
                values.append(float(match[metric_key]))
                plotted = True
            positions.append(
                x_positions[profile_index] - 0.4 + (idx + 0.5) * width
            )
        ax.bar(positions, values, width=width, label=clock)

    if not plotted:
        plt.close(fig)
        return

    ax.set_xticks(x_positions)
    ax.set_xticklabels(profiles)
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    ax.legend()
    ax.grid(axis="y", alpha=0.25)
    fig.tight_layout()
    fig.savefig(output_dir / filename, dpi=150)
    plt.close(fig)


def make_tradeoff_plot(rows: list[dict[str, object]], output_dir: Path) -> None:
    points: list[dict[str, object]] = []
    profiles = sorted({str(row["profile"]) for row in rows})
    for profile in profiles:
        dvv = next(
            (
                row
                for row in rows
                if str(row["profile"]) == profile and str(row["clock"]) == "dvv"
            ),
            None,
        )
        lease_dvv = next(
            (
                row
                for row in rows
                if str(row["profile"]) == profile and str(row["clock"]) == "lease_dvv"
            ),
            None,
        )
        if dvv is None or lease_dvv is None:
            continue

        baseline = float(dvv.get("metadata_representation.avg_metadata_bytes", 0.0))
        lease_value = float(lease_dvv.get("metadata_representation.avg_metadata_bytes", 0.0))
        violation_rate = float(lease_dvv.get("violations.violation_rate", 0.0))
        reduction_pct = ((baseline - lease_value) / baseline * 100.0) if baseline else 0.0
        points.append(
            {
                "profile": profile,
                "metadata_reduction_pct": round(reduction_pct, 3),
                "violation_rate": round(violation_rate, 4),
            }
        )

    write_csv(output_dir / "violation_rate_vs_metadata_reduction.csv", points)
    if not points:
        return

    fig, ax = plt.subplots(figsize=(8, 5))
    xs = [float(point["metadata_reduction_pct"]) for point in points]
    ys = [float(point["violation_rate"]) for point in points]
    ax.scatter(xs, ys, s=60)
    for point in points:
        ax.annotate(
            str(point["profile"]),
            (float(point["metadata_reduction_pct"]), float(point["violation_rate"])),
            xytext=(5, 5),
            textcoords="offset points",
        )
    ax.set_xlabel("Metadata reduction vs DVV (%)")
    ax.set_ylabel("Violation rate")
    ax.set_title("Lease-DVV Correctness Tradeoff")
    ax.grid(alpha=0.25)
    fig.tight_layout()
    fig.savefig(output_dir / "violation_rate_vs_metadata_reduction.png", dpi=150)
    plt.close(fig)


def write_time_series_report_plots(run_rows: list[dict[str, object]], output_dir: Path) -> None:
    time_plot_specs = [
        {
            "csv": "churn_over_time.csv",
            "x_key": "window_start",
            "y_key": "avg_active_nodes",
            "title": "Active Nodes Over Time",
            "ylabel": "Active nodes",
            "filename": "active_nodes_over_time_report.png",
        },
        {
            "csv": "metadata_representation.csv",
            "x_key": "window_start",
            "y_key": "avg_metadata_bytes",
            "title": "Metadata Bytes Over Time",
            "ylabel": "Metadata bytes",
            "filename": "metadata_bytes_over_time_report.png",
        },
        {
            "csv": "queue_length_over_time.csv",
            "x_key": "window_start",
            "y_key": "avg_queue_len",
            "title": "Queue Length Over Time",
            "ylabel": "Queue length",
            "filename": "queue_length_over_time_report.png",
        },
        {
            "csv": "violations_over_time.csv",
            "x_key": "window_start",
            "y_key": "violation_rate",
            "title": "Violation Rate Over Time",
            "ylabel": "Violation rate",
            "filename": "violations_over_time_report.png",
        },
    ]

    time_series_dir = output_dir / "time_series_report"
    time_series_dir.mkdir(parents=True, exist_ok=True)
    profiles = sorted({str(row["profile"]) for row in run_rows})
    clocks = sorted({str(row["clock"]) for row in run_rows})

    for spec in time_plot_specs:
        aggregated = aggregate_time_series(
            run_rows,
            relative_csv=spec["csv"],
            x_key=spec["x_key"],
            y_keys=[spec["y_key"]],
        )
        if not aggregated:
            continue

        write_csv(
            time_series_dir / spec["filename"].replace(".png", ".csv"),
            aggregated,
        )

        fig, axes = plt.subplots(
            len(profiles),
            1,
            figsize=(11, max(4, 3.5 * len(profiles))),
            sharex=True,
        )
        if len(profiles) == 1:
            axes = [axes]

        plotted = False
        for axis, profile in zip(axes, profiles):
            profile_rows = [row for row in aggregated if str(row["profile"]) == profile]
            for clock in clocks:
                clock_rows = [
                    row
                    for row in profile_rows
                    if str(row["clock"]) == clock and spec["y_key"] in row
                ]
                if not clock_rows:
                    continue
                axis.plot(
                    [float(row[spec["x_key"]]) for row in clock_rows],
                    [float(row[spec["y_key"]]) for row in clock_rows],
                    marker="o",
                    linewidth=2,
                    label=clock,
                )
                plotted = True
            axis.set_title(profile)
            axis.set_ylabel(spec["ylabel"])
            axis.grid(alpha=0.25)

        if not plotted:
            plt.close(fig)
            continue

        axes[0].legend(ncol=min(4, len(clocks)))
        axes[-1].set_xlabel("Simulation time")
        fig.suptitle(spec["title"])
        fig.tight_layout()
        fig.savefig(time_series_dir / spec["filename"], dpi=150)
        plt.close(fig)


def make_comparison_plots(rows: list[dict[str, object]], output_dir: Path) -> None:
    plot_specs = [
        (
            "metadata_representation.avg_metadata_bytes",
            "Average metadata bytes",
            "Metadata Cost vs Churn Profile",
            "metadata_bytes_vs_profile.png",
        ),
        (
            "stale_metadata.avg_stale_metadata_fraction",
            "Average stale metadata fraction",
            "Stale Metadata vs Churn Profile",
            "stale_metadata_fraction_vs_profile.png",
        ),
        (
            "queue_length.p95_time_weighted_queue_len",
            "P95 time-weighted queue length",
            "Buffering Penalty vs Churn Profile",
            "queue_p95_vs_profile.png",
        ),
        (
            "latency.latency_p95",
            "P95 latency",
            "Latency vs Churn Profile",
            "latency_p95_vs_profile.png",
        ),
        (
            "metadata_representation.avg_metadata_bytes_per_active_node",
            "Avg metadata bytes per active node",
            "Normalized Metadata Cost vs Churn Profile",
            "metadata_bytes_per_active_node_vs_profile.png",
        ),
        (
            "clock_state.avg_state_bytes_per_active_node",
            "Avg state bytes per active node",
            "Normalized Clock State vs Churn Profile",
            "state_bytes_per_active_node_vs_profile.png",
        ),
    ]
    for metric_key, ylabel, title, filename in plot_specs:
        grouped_profile_bar_plot(
            rows,
            metric_key=metric_key,
            ylabel=ylabel,
            title=title,
            filename=filename,
            output_dir=output_dir,
        )
    make_tradeoff_plot(rows, output_dir)


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
    parser.add(
        "--profiles",
        nargs="+",
        choices=["stable", "low", "sustained", "burst"],
        default=["stable", "sustained"],
    )
    parser.add("--seeds", nargs="+", type=int, default=[11, 22, 33])
    parser.add("--sim-time", type=float, default=300.0)
    parser.add("--initial-size", type=int, default=15)
    parser.add("--write-interval", type=float, default=20.0)
    parser.add("--max-nodes", type=int, default=40)
    parser.add("--min-nodes", type=int, default=5)
    parser.add("--min-lat", type=float, default=1.0)
    parser.add("--max-lat", type=float, default=5.0)
    parser.add("--key-count", type=int, default=5)
    parser.add("--hot-key-probability", type=float, default=0.8)
    parser.add("--replication-fanout", type=int, default=0)
    parser.add("--sample-interval", type=float, default=20.0)
    parser.add("--lease-duration", type=float, default=60.0)
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
        "hot_key_probability": args.hot_key_probability,
        "replication_fanout": args.replication_fanout,
        "sample_interval": args.sample_interval,
        "lease_duration": args.lease_duration,
        "analysis_window": args.analysis_window,
        "output_dir": args.output_dir,
        "experiment_name": args.experiment_name,
    }
    (experiment_dir / "experiment_config.json").write_text(
        json.dumps(experiment_config, indent=2)
    )

    run_rows: list[dict[str, object]] = []
    for profile in args.profiles:
        for clock in args.clocks:
            for seed in args.seeds:
                run_name = f"{profile}_{clock}_seed{seed}"
                run_dir = experiment_dir / run_name
                metrics = run_scenario(
                    profile=profile,
                    clock_factory=make_clock_factory(clock, args.lease_duration),
                    sim_time=args.sim_time,
                    seed=seed,
                    initial_size=args.initial_size,
                    write_interval=args.write_interval,
                    max_nodes=args.max_nodes,
                    min_nodes=args.min_nodes,
                    min_lat=args.min_lat,
                    max_lat=args.max_lat,
                    key_count=args.key_count,
                    hot_key_probability=args.hot_key_probability,
                    replication_fanout=args.replication_fanout,
                    sample_interval=args.sample_interval,
                    lease_duration=args.lease_duration,
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
                    "hot_key_probability": args.hot_key_probability,
                    "replication_fanout": args.replication_fanout,
                    "sample_interval": args.sample_interval,
                    "lease_duration": args.lease_duration,
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
    (experiment_dir / "comparison_by_clock.json").write_text(
        json.dumps(aggregated_rows, indent=2)
    )
    make_comparison_plots(aggregated_rows, experiment_dir)
    write_time_series_report_plots(run_rows, experiment_dir)


if __name__ == "__main__":
    main()
