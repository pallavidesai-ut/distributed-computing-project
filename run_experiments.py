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

WRITE_PDF = False

from analyze_run import analyze_run
from clocksim import (
    CLOCK_FACTORIES,
    make_clock_factory,
    run_scenario,
    save_run,
)


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


def load_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="") as handle:
        return list(csv.DictReader(handle))


def save_report_figure(fig: plt.Figure, output_path: Path) -> None:
    fig.savefig(output_path, dpi=180)
    if WRITE_PDF and output_path.suffix.lower() != ".pdf":
        fig.savefig(output_path.with_suffix(".pdf"))


def mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def sample_stderr(values: list[float]) -> float:
    if len(values) <= 1:
        return 0.0
    avg = mean(values)
    variance = sum((value - avg) ** 2 for value in values) / (len(values) - 1)
    return math.sqrt(variance) / math.sqrt(len(values))


def flatten_sections(sections: dict[str, dict[str, object]]) -> dict[str, object]:
    flat: dict[str, object] = {}
    for section, metrics in sections.items():
        for key, value in metrics.items():
            flat[f"{section}.{key}"] = value
    return flat


def lease_duration_label(lease_duration: float) -> str:
    text = f"{lease_duration:g}".replace(".", "p")
    return f"L{text}"


def is_lease_clock(clock: str) -> bool:
    return clock in {"lease_dvv", "membership_lease_dvv"}


def clock_variant(clock: str, lease_duration: float, lease_duration_count: int) -> str:
    if is_lease_clock(clock) and lease_duration_count > 1:
        return f"{clock}_{lease_duration_label(lease_duration)}"
    return clock


def aggregate_runs(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    grouped: dict[tuple[str, str], list[dict[str, object]]] = {}
    for row in rows:
        grouped.setdefault((str(row["profile"]), str(row["clock"])), []).append(row)

    aggregated: list[dict[str, object]] = []
    metric_keys = [
        key
        for key in rows[0].keys()
        if key
        not in {
            "profile",
            "clock",
            "clock_family",
            "seed",
            "run_name",
            "run_dir",
            "analysis_dir",
        }
    ]
    for (profile, clock), group in sorted(grouped.items()):
        row: dict[str, object] = {
            "profile": profile,
            "clock": clock,
            "clock_family": str(group[0].get("clock_family", clock)),
            "num_runs": len(group),
        }
        for metric in metric_keys:
            values = [
                float(candidate[metric])
                for candidate in group
                if isinstance(candidate.get(metric), (int, float))
            ]
            if values:
                row[metric] = round(mean(values), 4)
                row[f"{metric}.stderr"] = round(sample_stderr(values), 4)
        aggregated.append(row)
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
        errors: list[float] = []
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
            values.append(float(match[metric_key]) if match is not None else math.nan)
            errors.append(float(match.get(f"{metric_key}.stderr", 0.0)) if match is not None else 0.0)
            positions.append(x_positions[profile_index] - 0.4 + (idx + 0.5) * width)
            plotted = plotted or match is not None
        ax.bar(
            positions,
            values,
            width=width,
            yerr=errors,
            capsize=3,
            error_kw={"linewidth": 1, "alpha": 0.8},
            label=clock,
        )

    if not plotted:
        plt.close(fig)
        return

    ax.set_xticks(x_positions)
    ax.set_xticklabels(profiles)
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    ax.grid(axis="y", alpha=0.25)
    ax.legend()
    fig.tight_layout()
    save_report_figure(fig, output_dir / filename)
    plt.close(fig)


def aggregate_time_series(
    run_rows: list[dict[str, object]],
    *,
    relative_csv: str,
    x_key: str,
    y_key: str,
) -> list[dict[str, object]]:
    grouped: dict[tuple[str, str, float], list[float]] = {}
    for row in run_rows:
        analysis_dir = Path(str(row["analysis_dir"]))
        for point in load_csv(analysis_dir / relative_csv):
            key = (
                str(row["profile"]),
                str(row["clock"]),
                float(point[x_key]),
            )
            grouped.setdefault(key, []).append(float(point[y_key]))

    aggregated: list[dict[str, object]] = []
    for (profile, clock, x_value), values in sorted(grouped.items()):
        aggregated.append(
            {
                "profile": profile,
                "clock": clock,
                x_key: x_value,
                y_key: round(mean(values), 4),
            }
        )
    return aggregated


def write_time_series_report_plots(run_rows: list[dict[str, object]], output_dir: Path) -> None:
    specs = [
        (
            "metadata_over_time.csv",
            "window_start",
            "avg_metadata_bytes",
            "metadata_bytes_over_time_report.png",
            "Average metadata bytes",
            "Metadata bytes over time",
        ),
        (
            "accuracy_over_time.csv",
            "window_start",
            "avg_recall",
            "recall_over_time_report.png",
            "Average recall",
            "History recall over time",
        ),
        (
            "decision_quality_over_time.csv",
            "window_start",
            "missed_conflict_rate",
            "missed_conflicts_over_time_report.png",
            "Missed conflict rate",
            "Missed conflicts over time",
        ),
        (
            "replica_state_over_time.csv",
            "window_start",
            "avg_hot_key_siblings",
            "hot_key_siblings_over_time_report.png",
            "Average hot-key siblings",
            "Hot-key sibling count over time",
        ),
    ]
    time_series_dir = output_dir / "time_series_report"
    time_series_dir.mkdir(parents=True, exist_ok=True)
    profiles = sorted({str(row["profile"]) for row in run_rows})
    clocks = sorted({str(row["clock"]) for row in run_rows})

    for relative_csv, x_key, y_key, filename, ylabel, title in specs:
        aggregated = aggregate_time_series(run_rows, relative_csv=relative_csv, x_key=x_key, y_key=y_key)
        if not aggregated:
            continue
        write_csv(time_series_dir / filename.replace(".png", ".csv"), aggregated)

        fig, axes = plt.subplots(len(profiles), 1, figsize=(11, max(4, 3.5 * len(profiles))), sharex=True)
        if len(profiles) == 1:
            axes = [axes]

        plotted = False
        for axis, profile in zip(axes, profiles):
            profile_rows = [row for row in aggregated if str(row["profile"]) == profile]
            for clock in clocks:
                clock_rows = [row for row in profile_rows if str(row["clock"]) == clock]
                if not clock_rows:
                    continue
                axis.plot(
                    [float(row[x_key]) for row in clock_rows],
                    [float(row[y_key]) for row in clock_rows],
                    marker="o",
                    linewidth=2,
                    label=clock,
                )
                plotted = True
            axis.set_title(profile)
            axis.set_ylabel(ylabel)
            axis.grid(alpha=0.25)

        if not plotted:
            plt.close(fig)
            continue

        axes[0].legend(ncol=min(4, len(clocks)))
        axes[-1].set_xlabel("Simulation time")
        fig.suptitle(title)
        fig.tight_layout()
        save_report_figure(fig, time_series_dir / filename)
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
        lease_rows = [
            row
            for row in rows
            if str(row["profile"]) == profile
            and str(row.get("clock_family", row["clock"])) == "lease_dvv"
        ]
        if dvv is None or not lease_rows:
            continue
        dvv_bytes = float(dvv.get("metadata.avg_metadata_bytes", 0.0))
        for lease in lease_rows:
            lease_bytes = float(lease.get("metadata.avg_metadata_bytes", 0.0))
            reduction = ((dvv_bytes - lease_bytes) / dvv_bytes * 100.0) if dvv_bytes else 0.0
            recall_loss = max(
                float(dvv.get("accuracy.avg_recall", 0.0)) - float(lease.get("accuracy.avg_recall", 0.0)),
                0.0,
            )
            points.append(
                {
                    "profile": profile,
                    "clock": lease["clock"],
                    "lease_duration": lease.get("lease_duration", ""),
                    "metadata_reduction_pct": round(reduction, 3),
                    "recall_loss": round(recall_loss, 4),
                }
            )

    write_csv(output_dir / "metadata_reduction_vs_recall_loss.csv", points)
    if not points:
        return

    fig, ax = plt.subplots(figsize=(8, 5))
    xs = [float(point["metadata_reduction_pct"]) for point in points]
    ys = [float(point["recall_loss"]) for point in points]
    ax.scatter(xs, ys, s=60)
    for point in points:
        ax.annotate(
            f"{point['profile']} {point['clock']}",
            (float(point["metadata_reduction_pct"]), float(point["recall_loss"])),
            xytext=(5, 5),
            textcoords="offset points",
        )
    ax.set_xlabel("Lease-DVV metadata reduction vs DVV (%)")
    ax.set_ylabel("Recall loss")
    ax.set_title("Lease-DVV Tradeoff")
    ax.grid(alpha=0.25)
    fig.tight_layout()
    save_report_figure(fig, output_dir / "metadata_reduction_vs_recall_loss.png")
    plt.close(fig)


def make_lease_ablation_plots(rows: list[dict[str, object]], output_dir: Path) -> None:
    lease_rows = [
        row
        for row in rows
        if is_lease_clock(str(row.get("clock_family", row["clock"])))
        and isinstance(row.get("lease_duration"), (int, float))
    ]
    if not lease_rows:
        return

    table_rows: list[dict[str, object]] = []
    for row in sorted(lease_rows, key=lambda item: (str(item["profile"]), float(item["lease_duration"]))):
        table_rows.append(
            {
                "profile": row["profile"],
                "clock": row["clock"],
                "lease_duration": row["lease_duration"],
                "avg_metadata_bytes": row.get("metadata.avg_metadata_bytes", 0.0),
                "avg_recall": row.get("accuracy.avg_recall", 0.0),
                "missed_conflict_rate": row.get("decision_quality.missed_conflict_rate", 0.0),
                "stale_sibling_rate": row.get("decision_quality.stale_sibling_rate", 0.0),
                "pruned_write_rate": row.get("metadata.pruned_write_rate", 0.0),
            }
        )
    write_csv(output_dir / "lease_duration_ablation.csv", table_rows)

    specs = [
        ("metadata.avg_metadata_bytes", "Average metadata bytes", "lease_ablation_metadata.png"),
        ("accuracy.avg_recall", "Average history recall", "lease_ablation_recall.png"),
        ("decision_quality.stale_sibling_rate", "Stale sibling rate", "lease_ablation_stale_siblings.png"),
        ("metadata.pruned_write_rate", "Pruned write rate", "lease_ablation_pruning.png"),
    ]
    profiles = sorted({str(row["profile"]) for row in lease_rows})
    for metric_key, ylabel, filename in specs:
        fig, ax = plt.subplots(figsize=(8, 5))
        plotted = False
        for profile in profiles:
            profile_rows = [
                row
                for row in lease_rows
                if str(row["profile"]) == profile and metric_key in row
            ]
            profile_rows.sort(key=lambda item: float(item["lease_duration"]))
            if not profile_rows:
                continue
            ax.errorbar(
                [float(row["lease_duration"]) for row in profile_rows],
                [float(row[metric_key]) for row in profile_rows],
                yerr=[float(row.get(f"{metric_key}.stderr", 0.0)) for row in profile_rows],
                marker="o",
                linewidth=2,
                capsize=3,
                label=profile,
            )
            plotted = True
        if not plotted:
            plt.close(fig)
            continue
        ax.set_xlabel("Lease duration")
        ax.set_ylabel(ylabel)
        ax.set_title(f"Lease-DVV Ablation: {ylabel}")
        ax.grid(alpha=0.25)
        ax.legend()
        fig.tight_layout()
        save_report_figure(fig, output_dir / filename)
        plt.close(fig)


def make_headline_table(rows: list[dict[str, object]], output_dir: Path) -> None:
    by_profile_clock = {(str(row["profile"]), str(row["clock"])): row for row in rows}
    table: list[dict[str, object]] = []
    for profile in sorted({str(row["profile"]) for row in rows}):
        vv = by_profile_clock.get((profile, "vv"))
        dvv = by_profile_clock.get((profile, "dvv"))
        lease_l16 = by_profile_clock.get((profile, "lease_dvv_L16"))
        if vv is None or dvv is None:
            continue
        vv_bytes = float(vv.get("metadata.avg_metadata_bytes", 0.0))
        dvv_bytes = float(dvv.get("metadata.avg_metadata_bytes", 0.0))
        reduction = ((vv_bytes - dvv_bytes) / vv_bytes * 100.0) if vv_bytes else 0.0
        table.append(
            {
                "profile": profile,
                "vv_bytes_mean": round(vv_bytes, 4),
                "vv_bytes_stderr": vv.get("metadata.avg_metadata_bytes.stderr", 0.0),
                "dvv_bytes_mean": round(dvv_bytes, 4),
                "dvv_bytes_stderr": dvv.get("metadata.avg_metadata_bytes.stderr", 0.0),
                "dvv_reduction_vs_vv_pct": round(reduction, 3),
                "vv_recall": vv.get("accuracy.avg_recall", 0.0),
                "dvv_recall": dvv.get("accuracy.avg_recall", 0.0),
                "lease_l16_bytes_mean": lease_l16.get("metadata.avg_metadata_bytes", "") if lease_l16 else "",
                "lease_l16_bytes_stderr": lease_l16.get("metadata.avg_metadata_bytes.stderr", "") if lease_l16 else "",
                "lease_l16_recall": lease_l16.get("accuracy.avg_recall", "") if lease_l16 else "",
            }
        )
    write_csv(output_dir / "headline_results.csv", table)


def make_failure_mode_outputs(rows: list[dict[str, object]], output_dir: Path) -> None:
    table: list[dict[str, object]] = []
    for row in rows:
        table.append(
            {
                "profile": row["profile"],
                "clock": row["clock"],
                "avg_precision": row.get("accuracy.avg_precision", 0.0),
                "avg_recall": row.get("accuracy.avg_recall", 0.0),
                "avg_false_positive_events": row.get("accuracy.avg_false_positive_events", 0.0),
                "avg_false_negative_events": row.get("accuracy.avg_false_negative_events", 0.0),
                "missed_conflict_rate": row.get("decision_quality.missed_conflict_rate", 0.0),
                "stale_sibling_rate": row.get("decision_quality.stale_sibling_rate", 0.0),
            }
        )
    write_csv(output_dir / "failure_modes_by_clock.csv", table)

    clocks = sorted({str(row["clock"]) for row in rows})
    false_positive = [mean([float(row.get("accuracy.avg_false_positive_events", 0.0)) for row in rows if str(row["clock"]) == clock]) for clock in clocks]
    false_negative = [mean([float(row.get("accuracy.avg_false_negative_events", 0.0)) for row in rows if str(row["clock"]) == clock]) for clock in clocks]
    x_positions = list(range(len(clocks)))
    width = 0.38
    fig, ax = plt.subplots(figsize=(10, 5))
    ax.bar([x - width / 2 for x in x_positions], false_positive, width=width, label="False positives")
    ax.bar([x + width / 2 for x in x_positions], false_negative, width=width, label="False negatives")
    ax.set_xticks(x_positions)
    ax.set_xticklabels(clocks, rotation=20, ha="right")
    ax.set_ylabel("Average events per version")
    ax.set_title("Clock Failure Modes: Invented vs Forgotten Ancestry")
    ax.grid(axis="y", alpha=0.25)
    ax.legend()
    fig.tight_layout()
    save_report_figure(fig, output_dir / "false_positive_negative_by_clock.png")
    plt.close(fig)


def make_pareto_plot(rows: list[dict[str, object]], output_dir: Path) -> None:
    fig, ax = plt.subplots(figsize=(8, 5))
    markers = {"stable": "o", "low": "s", "sustained": "^", "burst": "D"}
    plotted_labels: set[str] = set()
    for row in rows:
        clock = str(row["clock"])
        profile = str(row["profile"])
        label = clock if clock not in plotted_labels else "_nolegend_"
        ax.scatter(
            float(row.get("metadata.avg_metadata_bytes", 0.0)),
            float(row.get("accuracy.avg_recall", 0.0)),
            marker=markers.get(profile, "o"),
            s=55,
            label=label,
        )
        plotted_labels.add(clock)
    ax.set_xlabel("Average metadata bytes")
    ax.set_ylabel("Average history recall")
    ax.set_title("Metadata/Recall Pareto View")
    ax.grid(alpha=0.25)
    ax.legend(ncol=2)
    fig.tight_layout()
    save_report_figure(fig, output_dir / "metadata_recall_pareto.png")
    plt.close(fig)


def make_comparison_plots(rows: list[dict[str, object]], output_dir: Path) -> None:
    specs = [
        ("metadata.avg_metadata_bytes", "Avg metadata bytes", "Metadata Cost by Churn Profile", "metadata_bytes_vs_profile.png"),
        ("accuracy.avg_precision", "Average ancestry precision", "History Precision by Churn Profile", "precision_vs_profile.png"),
        ("accuracy.avg_recall", "Average ancestry recall", "History Recall by Churn Profile", "recall_vs_profile.png"),
        ("decision_quality.missed_conflict_rate", "Missed conflict rate", "Conflict Loss by Churn Profile", "missed_conflicts_vs_profile.png"),
        ("decision_quality.stale_sibling_rate", "Stale sibling rate", "Stale Sibling Retention by Churn Profile", "stale_siblings_vs_profile.png"),
        ("replica_state.avg_hot_key_siblings", "Avg hot-key siblings", "Sibling Pressure by Churn Profile", "hot_key_siblings_vs_profile.png"),
        ("replica_state.avg_stale_actor_fraction", "Avg stale replica-actor fraction", "Stale Replica Actor Pressure by Churn Profile", "stale_actor_fraction_vs_profile.png"),
    ]
    for metric_key, ylabel, title, filename in specs:
        grouped_profile_bar_plot(
            rows,
            metric_key=metric_key,
            ylabel=ylabel,
            title=title,
            filename=filename,
            output_dir=output_dir,
        )
    make_tradeoff_plot(rows, output_dir)
    make_lease_ablation_plots(rows, output_dir)
    make_headline_table(rows, output_dir)
    make_failure_mode_outputs(rows, output_dir)
    make_pareto_plot(rows, output_dir)


def clock_track(clock: str) -> str:
    if clock == "vv":
        return "Exact Baseline"
    if clock == "dvv":
        return "DVV"
    if clock == "itc":
        return "Interval Tree Clock"
    if clock.startswith("lease_dvv"):
        return "Approximate DVV"
    return "Other"


def build_report(rows: list[dict[str, object]], output_dir: Path, experiment_config: dict[str, object]) -> None:
    profiles = sorted({str(row["profile"]) for row in rows})
    clocks = sorted({str(row["clock"]) for row in rows})
    lines = [
        "# Clock Comparison Report",
        "",
        "## Scope",
        "",
        "This experiment matrix compares exact VV, exact DVV, exact ITC when selected, and lease-pruned DVV under the same churn-heavy workload.",
        "",
        "The simulator now separates true causal history from clock-encoded history, which makes the comparison meaningful on three axes:",
        "",
        "- metadata cost per write and per stored version",
        "- conflict-handling accuracy on hot keys",
        "- ancestry loss introduced by lease pruning",
        "",
        "## Clock Map",
        "",
        "| Clock | Encoded state | Main strength | Main failure mode under churn |",
        "| --- | --- | --- | --- |",
        "| VV | Exact per-object vector over bounded client actors | Full ancestry precision with the simplest semantics | Metadata grows with the number of distinct clients touching an object |",
        "| DVV | Prefix summary plus explicit dots over replica actors | Full ancestry precision with metadata bounded by replication degree | More complex representation and implementation |",
        "| ITC | Interval Tree Clock identity and event trees over dynamic client actors | Exact dynamic-actor causality without a fixed vector dimension | More complex tree representation; metadata depends on actor allocation/history shape |",
        "| Lease-DVV | DVV with actor-expiry pruning before new writes | Cuts stale metadata aggressively under churn | Can forget old ancestry and retain stale siblings or lose recall |",
        "",
        "Related alternatives worth discussing in the paper, but not implemented here, are Bounded Version Vectors and HLC-style approximate causality if the study broadens beyond exact version ancestry.",
        "",
        "## Benchmark Design",
        "",
        f"- Profiles: {', '.join(profiles)}",
        f"- Clocks: {', '.join(clocks)}",
        f"- Seeds: {experiment_config['seeds']}",
        f"- Lease-DVV durations: {experiment_config['lease_durations']}",
        f"- Hot-key probability: {experiment_config['hot_key_probability']}",
        f"- Client actor pool: {experiment_config['client_count']}",
        f"- Replication factor: {experiment_config['replication_factor']}",
        f"- Contention burst every {experiment_config['burst_interval']} time units with {experiment_config['burst_writers']} writers",
        "",
        "Each run mixes background read-then-write traffic with explicit hot-key contention bursts and later merge writes. Exact VV uses a bounded client actor pool with carried per-key session context, and DVV/lease-DVV use replica dots. This keeps the main comparison on the same per-object causality semantics.",
        "",
        "## Aggregated Findings",
        "",
    ]

    lines.extend(
        [
            "### Track Structure",
            "",
            "- Apples-to-apples exact track: compare `vv` vs `dvv` vs `itc` when selected.",
            "- Approximate track: compare exact clocks against all `lease_dvv` variants.",
            "",
        ]
    )

    for profile in profiles:
        lines.append(f"### {profile}")
        lines.append("")
        for clock in clocks:
            row = next(
                item
                for item in rows
                if str(item["profile"]) == profile and str(item["clock"]) == clock
            )
            lines.append(
                f"- `{clock}` ({clock_track(clock)}): metadata {row.get('metadata.avg_metadata_bytes', 0):.2f} B, "
                f"precision {row.get('accuracy.avg_precision', 0):.3f}, "
                f"recall {row.get('accuracy.avg_recall', 0):.3f}, "
                f"missed conflicts {row.get('decision_quality.missed_conflict_rate', 0):.3f}, "
                f"stale siblings {row.get('decision_quality.stale_sibling_rate', 0):.3f}"
            )
        lines.append("")

    lines.extend(
        [
            "## Interpretation",
            "",
            "- `vv` is the exact vanilla baseline. It shows the metadata cost of preserving full object ancestry with bounded client actors.",
            "- `dvv` should match `vv` on correctness while reducing metadata by using replica-issued dots rather than tracking every client actor in the version vector.",
            "- `itc` is exact and uses real Interval Tree Clock fork/event/join/compare semantics over dynamic client actors; compare its tree metadata against the exact VV and DVV baselines.",
            "- `lease_dvv` is the only intentionally approximate design. Its value depends on whether the extra metadata savings over exact DVV justify the ancestry recall loss across lease durations.",
            "",
            "## Outputs",
            "",
            "- `comparison_by_clock.csv`: aggregated metrics by churn profile and clock",
            "- `lease_duration_ablation.csv`: lease-DVV metadata/correctness ablation table",
            "- `time_series_report/`: profile-by-profile time-series plots",
            "- `metadata_reduction_vs_recall_loss.png`: lease-DVV tradeoff summary",
            "- `lease_ablation_*.png`: lease-duration sensitivity plots",
        ]
    )

    (output_dir / "study_report.md").write_text("\n".join(lines))


def build_parser() -> configargparse.ArgParser:
    parser = configargparse.ArgParser(
        description="Run a repeatable per-object clock comparison matrix.",
        default_config_files=["configs/full_experiment.yaml"],
        config_file_parser_class=configargparse.YAMLConfigFileParser,
    )
    parser.add("-c", "--config", is_config_file=True, help="Path to a YAML config file.")
    parser.add("--clocks", nargs="+", choices=sorted(CLOCK_FACTORIES), default=["vv", "dvv", "lease_dvv"])
    parser.add("--profiles", nargs="+", choices=["stable", "low", "sustained", "burst"], default=["stable", "sustained", "burst"])
    parser.add("--seeds", nargs="+", type=int, default=[7, 17, 29])
    parser.add("--sim-time", type=float, default=240.0)
    parser.add("--initial-size", type=int, default=10)
    parser.add("--write-interval", type=float, default=5.0)
    parser.add("--client-think-time", type=float, default=4.0)
    parser.add("--merge-probability", type=float, default=0.35)
    parser.add("--burst-interval", type=float, default=18.0)
    parser.add("--burst-writers", type=int, default=4)
    parser.add("--burst-spread", type=float, default=2.0)
    parser.add("--merge-delay", type=float, default=10.0)
    parser.add("--same-coordinator-probability", type=float, default=0.75)
    parser.add("--max-nodes", type=int, default=28)
    parser.add("--min-nodes", type=int, default=4)
    parser.add("--min-lat", type=float, default=1.0)
    parser.add("--max-lat", type=float, default=5.0)
    parser.add("--key-count", type=int, default=12)
    parser.add("--hot-key-probability", type=float, default=0.65)
    parser.add("--client-count", type=int, default=128)
    parser.add("--replication-factor", type=int, default=4)
    parser.add("--sample-interval", type=float, default=10.0)
    parser.add("--lease-duration", type=float, default=16.0)
    parser.add("--lease-durations", nargs="+", type=float, default=None)
    parser.add("--analysis-window", type=float, default=12.0)
    parser.add("--write-pdf", action="store_true", help="Also write PDF copies of generated report plots.")
    parser.add("--output-dir", default="output/experiments")
    parser.add("--experiment-name", default="per_object_clock_study")
    return parser


def main() -> None:
    global WRITE_PDF
    args = build_parser().parse_args()
    WRITE_PDF = args.write_pdf
    lease_durations = args.lease_durations or [args.lease_duration]
    experiment_dir = Path(args.output_dir) / args.experiment_name
    experiment_dir.mkdir(parents=True, exist_ok=True)

    experiment_config = {
        "clocks": args.clocks,
        "profiles": args.profiles,
        "seeds": args.seeds,
        "sim_time": args.sim_time,
        "initial_size": args.initial_size,
        "write_interval": args.write_interval,
        "client_think_time": args.client_think_time,
        "merge_probability": args.merge_probability,
        "burst_interval": args.burst_interval,
        "burst_writers": args.burst_writers,
        "burst_spread": args.burst_spread,
        "merge_delay": args.merge_delay,
        "same_coordinator_probability": args.same_coordinator_probability,
        "max_nodes": args.max_nodes,
        "min_nodes": args.min_nodes,
        "min_lat": args.min_lat,
        "max_lat": args.max_lat,
        "key_count": args.key_count,
        "hot_key_probability": args.hot_key_probability,
        "client_count": args.client_count,
        "replication_factor": args.replication_factor,
        "sample_interval": args.sample_interval,
        "lease_duration": lease_durations[0],
        "lease_durations": lease_durations,
        "analysis_window": args.analysis_window,
        "write_pdf": args.write_pdf,
        "output_dir": args.output_dir,
        "experiment_name": args.experiment_name,
    }
    (experiment_dir / "experiment_config.json").write_text(json.dumps(experiment_config, indent=2))

    run_rows: list[dict[str, object]] = []
    for profile in args.profiles:
        for clock in args.clocks:
            clock_lease_durations = lease_durations if is_lease_clock(clock) else [lease_durations[0]]
            for lease_duration in clock_lease_durations:
                variant = clock_variant(clock, lease_duration, len(clock_lease_durations))
                for seed in args.seeds:
                    run_name = f"{profile}_{variant}_seed{seed}"
                    run_dir = experiment_dir / run_name
                    metrics = run_scenario(
                        profile=profile,
                        clock_factory=make_clock_factory(clock, lease_duration),
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
                        client_count=args.client_count,
                        replication_factor=args.replication_factor,
                        sample_interval=args.sample_interval,
                        client_think_time=args.client_think_time,
                        merge_probability=args.merge_probability,
                        burst_interval=args.burst_interval,
                        burst_writers=args.burst_writers,
                        burst_spread=args.burst_spread,
                        merge_delay=args.merge_delay,
                        same_coordinator_probability=args.same_coordinator_probability,
                    )
                    run_config = {
                        **experiment_config,
                        "profile": profile,
                        "clock": clock,
                        "clock_variant": variant,
                        "seed": seed,
                        "lease_duration": lease_duration if is_lease_clock(clock) else None,
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
                        "clock": variant,
                        "clock_family": clock,
                        "lease_duration": lease_duration if is_lease_clock(clock) else "",
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
    make_comparison_plots(aggregated_rows, experiment_dir)
    write_time_series_report_plots(run_rows, experiment_dir)
    build_report(aggregated_rows, experiment_dir, experiment_config)


if __name__ == "__main__":
    main()
