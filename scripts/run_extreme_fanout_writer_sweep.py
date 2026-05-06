#!/usr/bin/env python3
"""Sweep hot-key writer fanout and plot VV/DVV/lease-DVV tradeoffs."""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import subprocess
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    import yaml
except ModuleNotFoundError:  # pragma: no cover - depends on local Python env
    yaml = None

os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib")
os.environ.setdefault("XDG_CACHE_HOME", "/tmp/.cache")

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
VENV_PYTHON = REPO_ROOT / ".venv" / "bin" / "python"
if VENV_PYTHON.exists() and Path(sys.executable).resolve() != VENV_PYTHON.resolve():
    os.execv(str(VENV_PYTHON), [str(VENV_PYTHON), str(Path(__file__).resolve()), *sys.argv[1:]])

from clocksim import (
    CLOCK_FACTORIES,
    CHURN_PROFILES,
    make_clock_factory,
    run_scenario,
    save_run,
    scenario_config_from_kwargs,
    scenario_config_to_dict,
)


OBJECT_METADATA_KEY = "replica_state.avg_sibling_set_metadata_bytes"
WRITE_STAMP_METADATA_KEY = "metadata.avg_metadata_bytes"
ACCURACY_RECALL_KEY = "accuracy.avg_recall"
STALE_SIBLING_KEY = "decision_quality.stale_sibling_rate"
MISSED_CONFLICT_KEY = "decision_quality.missed_conflict_rate"
HOT_SIBLINGS_KEY = "replica_state.avg_hot_key_siblings"
PRUNED_WRITE_KEY = "metadata.pruned_write_rate"


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def sample_stderr(values: list[float]) -> float:
    if len(values) <= 1:
        return 0.0
    avg = mean(values)
    variance = sum((value - avg) ** 2 for value in values) / (len(values) - 1)
    return math.sqrt(variance) / math.sqrt(len(values))


def as_float(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def pct_reduction(baseline: float, candidate: float) -> float:
    if baseline <= 0.0:
        return 0.0
    return round((1.0 - candidate / baseline) * 100.0, 3)


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
    return clock in {
        "adaptive_lease_dvv",
        "lease_dvv",
        "lease_dvv_client",
        "membership_lease_dvv",
    }


def clock_variant(clock: str, lease_duration: float, lease_duration_count: int) -> str:
    if is_lease_clock(clock) and lease_duration_count > 1:
        return f"{clock}_{lease_duration_label(lease_duration)}"
    return clock


def git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return "unknown"


def parse_scalar(value: str) -> Any:
    value = value.strip()
    if not value:
        return ""
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [parse_scalar(part.strip()) for part in inner.split(",")]
    if value.lower() in {"true", "false"}:
        return value.lower() == "true"
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value.strip("\"'")


def load_config_without_yaml(path: Path) -> dict[str, Any]:
    """Parse the simple top-level YAML shape used by experiment configs."""

    parsed: dict[str, Any] = {}
    current_key: str | None = None
    for raw_line in path.read_text().splitlines():
        line = raw_line.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        stripped = line.strip()
        if stripped.startswith("-") and current_key is not None:
            parsed.setdefault(current_key, []).append(parse_scalar(stripped[1:].strip()))
            continue
        if ":" not in line:
            current_key = None
            continue
        key, raw_value = line.split(":", 1)
        current_key = key.strip()
        value = raw_value.strip()
        parsed[current_key] = [] if not value else parse_scalar(value)
    return parsed


def load_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    if yaml is not None:
        return yaml.safe_load(path.read_text()) or {}
    return load_config_without_yaml(path)


def config_get(config: dict[str, Any], key: str, default: Any) -> Any:
    return config.get(key, config.get(key.replace("-", "_"), default))


def aggregate_by_fanout(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    grouped: dict[tuple[int, str, str], list[dict[str, object]]] = {}
    for row in rows:
        grouped.setdefault(
            (
                int(row["burst_writers"]),
                str(row["profile"]),
                str(row["clock"]),
            ),
            [],
        ).append(row)

    skip = {
        "burst_writers",
        "profile",
        "clock",
        "clock_family",
        "lease_duration",
        "seed",
        "run_name",
        "run_dir",
        "analysis_dir",
    }
    metric_keys = sorted({key for row in rows for key in row if key not in skip})
    aggregated: list[dict[str, object]] = []
    for (burst_writers, profile, clock), group in sorted(grouped.items()):
        record: dict[str, object] = {
            "burst_writers": burst_writers,
            "profile": profile,
            "clock": clock,
            "clock_family": str(group[0].get("clock_family", clock)),
            "lease_duration": group[0].get("lease_duration", ""),
            "num_runs": len(group),
        }
        for metric in metric_keys:
            values = [
                parsed
                for parsed in (as_float(candidate.get(metric)) for candidate in group)
                if parsed is not None
            ]
            if values:
                record[metric] = round(mean(values), 4)
                record[f"{metric}.stderr"] = round(sample_stderr(values), 4)
        aggregated.append(record)
    return aggregated


def exact_summary(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    by_key = {
        (int(row["burst_writers"]), str(row["profile"]), str(row["clock"])): row
        for row in rows
    }
    summary: list[dict[str, object]] = []
    pairs = sorted({(int(row["burst_writers"]), str(row["profile"])) for row in rows})
    for burst_writers, profile in pairs:
        vv = by_key.get((burst_writers, profile, "vv"))
        dvv = by_key.get((burst_writers, profile, "dvv"))
        if vv is None or dvv is None:
            continue
        vv_write = float(vv.get(WRITE_STAMP_METADATA_KEY, 0.0))
        dvv_write = float(dvv.get(WRITE_STAMP_METADATA_KEY, 0.0))
        vv_object = float(vv.get(OBJECT_METADATA_KEY, 0.0))
        dvv_object = float(dvv.get(OBJECT_METADATA_KEY, 0.0))
        summary.append(
            {
                "profile": profile,
                "burst_writers": burst_writers,
                "vv_write_bytes": round(vv_write, 4),
                "dvv_write_bytes": round(dvv_write, 4),
                "dvv_write_reduction_vs_vv_pct": pct_reduction(vv_write, dvv_write),
                "vv_sibling_set_bytes": round(vv_object, 4),
                "dvv_sibling_set_bytes": round(dvv_object, 4),
                "dvv_sibling_set_reduction_vs_vv_pct": pct_reduction(vv_object, dvv_object),
                "vv_recall": vv.get(ACCURACY_RECALL_KEY, 0.0),
                "dvv_recall": dvv.get(ACCURACY_RECALL_KEY, 0.0),
                "vv_stale_sibling_rate": vv.get(STALE_SIBLING_KEY, 0.0),
                "dvv_stale_sibling_rate": dvv.get(STALE_SIBLING_KEY, 0.0),
            }
        )
    return summary


def lease_tradeoff_summary(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    by_key = {
        (int(row["burst_writers"]), str(row["profile"]), str(row["clock"])): row
        for row in rows
    }
    output: list[dict[str, object]] = []
    for row in rows:
        if not is_lease_clock(str(row.get("clock_family", row["clock"]))):
            continue
        burst_writers = int(row["burst_writers"])
        profile = str(row["profile"])
        dvv = by_key.get((burst_writers, profile, "dvv"))
        if dvv is None:
            continue
        dvv_write = float(dvv.get(WRITE_STAMP_METADATA_KEY, 0.0))
        lease_write = float(row.get(WRITE_STAMP_METADATA_KEY, 0.0))
        dvv_object = float(dvv.get(OBJECT_METADATA_KEY, 0.0))
        lease_object = float(row.get(OBJECT_METADATA_KEY, 0.0))
        dvv_recall = float(dvv.get(ACCURACY_RECALL_KEY, 0.0))
        lease_recall = float(row.get(ACCURACY_RECALL_KEY, 0.0))
        output.append(
            {
                "profile": profile,
                "burst_writers": burst_writers,
                "clock": row["clock"],
                "lease_duration": row.get("lease_duration", ""),
                "lease_write_reduction_vs_dvv_pct": pct_reduction(dvv_write, lease_write),
                "lease_sibling_set_reduction_vs_dvv_pct": pct_reduction(dvv_object, lease_object),
                "dvv_recall": round(dvv_recall, 4),
                "lease_recall": round(lease_recall, 4),
                "recall_loss": round(max(dvv_recall - lease_recall, 0.0), 4),
                "lease_stale_sibling_rate": row.get(STALE_SIBLING_KEY, 0.0),
                "lease_pruned_write_rate": row.get(PRUNED_WRITE_KEY, 0.0),
            }
        )
    return output


def require_pyplot() -> Any:
    try:
        import matplotlib.pyplot as plt
    except ModuleNotFoundError as exc:  # pragma: no cover - environment guard
        raise SystemExit(
            "matplotlib is required to generate fanout plots. "
            "Run this script with the project virtualenv, e.g. "
            "`.venv/bin/python scripts/run_extreme_fanout_writer_sweep.py ...`."
        ) from exc
    return plt


def save_figure(fig: Any, path: Path, *, write_pdf: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=180)
    if write_pdf:
        fig.savefig(path.with_suffix(".pdf"))


def plot_metric_by_fanout(
    rows: list[dict[str, object]],
    *,
    metric_key: str,
    ylabel: str,
    title: str,
    output_path: Path,
    write_pdf: bool,
) -> None:
    plt = require_pyplot()
    profiles = sorted({str(row["profile"]) for row in rows})
    clocks = sorted({str(row["clock"]) for row in rows})
    writer_counts = sorted({int(row["burst_writers"]) for row in rows})
    if not profiles or not clocks or not writer_counts:
        return

    fig, axes = plt.subplots(
        len(profiles),
        1,
        figsize=(10, max(4, 3.2 * len(profiles))),
        sharex=True,
    )
    if len(profiles) == 1:
        axes = [axes]

    plotted = False
    for axis, profile in zip(axes, profiles):
        profile_rows = [row for row in rows if str(row["profile"]) == profile]
        for clock in clocks:
            clock_rows = [
                row
                for row in profile_rows
                if str(row["clock"]) == clock and metric_key in row
            ]
            clock_rows.sort(key=lambda item: int(item["burst_writers"]))
            if not clock_rows:
                continue
            axis.errorbar(
                [int(row["burst_writers"]) for row in clock_rows],
                [float(row[metric_key]) for row in clock_rows],
                yerr=[float(row.get(f"{metric_key}.stderr", 0.0)) for row in clock_rows],
                marker="o",
                linewidth=2,
                capsize=3,
                label=clock,
            )
            plotted = True
        axis.set_xscale("log", base=2)
        axis.set_xticks(writer_counts)
        axis.set_xticklabels([str(count) for count in writer_counts])
        axis.set_title(profile)
        axis.set_ylabel(ylabel)
        axis.grid(alpha=0.25)

    if not plotted:
        plt.close(fig)
        return
    axes[0].legend(ncol=min(3, len(clocks)))
    axes[-1].set_xlabel("Concurrent burst writers")
    fig.suptitle(title)
    fig.tight_layout()
    save_figure(fig, output_path, write_pdf=write_pdf)
    plt.close(fig)


def plot_exact_reduction(summary_rows: list[dict[str, object]], output_path: Path, *, write_pdf: bool) -> None:
    plt = require_pyplot()
    profiles = sorted({str(row["profile"]) for row in summary_rows})
    if not profiles:
        return
    fig, ax = plt.subplots(figsize=(9, 5))
    for profile in profiles:
        profile_rows = [row for row in summary_rows if str(row["profile"]) == profile]
        profile_rows.sort(key=lambda item: int(item["burst_writers"]))
        ax.plot(
            [int(row["burst_writers"]) for row in profile_rows],
            [float(row["dvv_sibling_set_reduction_vs_vv_pct"]) for row in profile_rows],
            marker="o",
            linewidth=2,
            label=profile,
        )
    ax.axhline(0.0, color="#555555", linewidth=0.8)
    ax.set_xscale("log", base=2)
    ax.set_xlabel("Concurrent burst writers")
    ax.set_ylabel("DVV sibling-set reduction vs VV (%)")
    ax.set_title("Exact DVV Shared-Context Benefit")
    ax.grid(alpha=0.25)
    ax.legend()
    fig.tight_layout()
    save_figure(fig, output_path, write_pdf=write_pdf)
    plt.close(fig)


def plot_lease_tradeoff(summary_rows: list[dict[str, object]], output_path: Path, *, write_pdf: bool) -> None:
    plt = require_pyplot()
    if not summary_rows:
        return
    profiles = sorted({str(row["profile"]) for row in summary_rows})
    fig, axes = plt.subplots(
        len(profiles),
        1,
        figsize=(10, max(4, 3.4 * len(profiles))),
        sharex=True,
    )
    if len(profiles) == 1:
        axes = [axes]
    for axis, profile in zip(axes, profiles):
        profile_rows = [row for row in summary_rows if str(row["profile"]) == profile]
        clocks = sorted({str(row["clock"]) for row in profile_rows})
        for clock in clocks:
            clock_rows = [row for row in profile_rows if str(row["clock"]) == clock]
            clock_rows.sort(key=lambda item: int(item["burst_writers"]))
            axis.plot(
                [int(row["burst_writers"]) for row in clock_rows],
                [float(row["recall_loss"]) for row in clock_rows],
                marker="o",
                linewidth=2,
                label=f"{clock} recall loss",
            )
            axis.plot(
                [int(row["burst_writers"]) for row in clock_rows],
                [float(row["lease_sibling_set_reduction_vs_dvv_pct"]) / 100.0 for row in clock_rows],
                marker="s",
                linestyle="--",
                linewidth=1.6,
                label=f"{clock} metadata reduction / 100",
            )
        axis.set_xscale("log", base=2)
        axis.set_title(profile)
        axis.set_ylabel("Recall loss / scaled reduction")
        axis.grid(alpha=0.25)
    axes[0].legend(ncol=2)
    axes[-1].set_xlabel("Concurrent burst writers")
    fig.suptitle("Lease-DVV Fanout Tradeoff")
    fig.tight_layout()
    save_figure(fig, output_path, write_pdf=write_pdf)
    plt.close(fig)


def write_report(
    experiment_dir: Path,
    *,
    experiment_config: dict[str, object],
    aggregate_files: list[str],
    figure_files: list[str],
) -> None:
    lines = [
        "# Extreme Fanout Writer Sweep",
        "",
        "This experiment stresses hot-key sibling fanout by sweeping concurrent burst writers.",
        "It compares exact VV and exact DVV over the same actor domain, and includes lease-DVV as an approximate metadata-vs-recall point.",
        "",
        "## Design",
        "",
        f"- Actor domain: `{experiment_config['actor_domain']}`",
        f"- Profiles: {', '.join(str(item) for item in experiment_config['profiles'])}",
        f"- Clocks: {', '.join(str(item) for item in experiment_config['clocks'])}",
        f"- Writer counts: {', '.join(str(item) for item in experiment_config['writer_counts'])}",
        f"- Seeds: {', '.join(str(item) for item in experiment_config['seeds'])}",
        f"- Lease durations: {', '.join(str(item) for item in experiment_config['lease_durations'])}",
        f"- Key count: {experiment_config['key_count']} with hot-key probability {experiment_config['hot_key_probability']}",
        f"- Replication factor: {experiment_config['replication_factor']}",
        "",
        "## Reading The Plots",
        "",
        "- `sibling_set_metadata_bytes_vs_writers.png` is the main DVV figure: VV repeats vectors across siblings, while DVV can share common context and store one dot per sibling.",
        "- `recall_vs_writers.png` and `stale_sibling_rate_vs_writers.png` show the correctness side, especially for lease-DVV.",
        "- `lease_fanout_tradeoff.png` overlays lease metadata reduction against recall loss; exact DVV is the reference point.",
        "",
        "## Outputs",
        "",
        *[f"- `{path}`" for path in aggregate_files],
        *[f"- `{path}`" for path in figure_files],
        "",
    ]
    (experiment_dir / "study_report.md").write_text("\n".join(lines))


def run_experiment_case(spec: dict[str, object]) -> dict[str, object]:
    from analyze_run import analyze_run

    profile = str(spec["profile"])
    clock = str(spec["clock"])
    variant = str(spec["variant"])
    lease_duration = float(spec["lease_duration"])
    seed = int(spec["seed"])
    run_name = str(spec["run_name"])
    run_dir = Path(str(spec["run_dir"]))
    scenario_config = spec["scenario_config"]
    experiment_config = spec["experiment_config"]
    analysis_window = float(spec["analysis_window"])

    metrics = run_scenario(
        config=scenario_config,
        clock_factory=make_clock_factory(clock, lease_duration),
    )
    run_config = {
        **experiment_config,
        **scenario_config_to_dict(scenario_config),
        "clock": clock,
        "clock_variant": variant,
        "lease_duration": lease_duration if is_lease_clock(clock) else None,
        "swept_burst_writers": int(spec["burst_writers"]),
    }
    save_run(
        metrics,
        output_dir=run_dir,
        run_name=run_name,
        config=run_config,
        sim_time=scenario_config.sim_time,
    )
    analysis_dir = run_dir / f"{run_name}_analysis"
    sections = analyze_run(
        input_dir=run_dir,
        run_name=run_name,
        window=analysis_window,
        output_dir=analysis_dir,
    )
    row = {
        "_order": int(spec["order"]),
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
    return row


def run_fanout_case(spec: dict[str, object]) -> dict[str, object]:
    row = run_experiment_case(spec)
    order = row.pop("_order")
    return {
        "_order": order,
        "burst_writers": spec["burst_writers"],
        **row,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run an extreme hot-key writer fanout sweep for VV/DVV/lease-DVV."
    )
    parser.add_argument("--config", default="configs/dvv_sibling_fanout_study.yaml")
    parser.add_argument("--writer-counts", nargs="+", type=int, default=None)
    parser.add_argument("--clocks", nargs="+", choices=sorted(CLOCK_FACTORIES), default=None)
    parser.add_argument("--profiles", nargs="+", choices=sorted(CHURN_PROFILES), default=None)
    parser.add_argument("--seeds", nargs="+", type=int, default=None)
    parser.add_argument("--lease-duration", type=float, default=None)
    parser.add_argument("--lease-durations", nargs="+", type=float, default=None)
    parser.add_argument("--fixed-lease-duration", action="store_true")
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--progress", action="store_true")
    parser.add_argument("--write-pdf", action="store_true")
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--experiment-name", default=None)

    parser.add_argument("--sim-time", type=float, default=None)
    parser.add_argument("--initial-size", type=int, default=None)
    parser.add_argument("--max-nodes", type=int, default=None)
    parser.add_argument("--min-nodes", type=int, default=None)
    parser.add_argument("--replication-factor", type=int, default=None)
    parser.add_argument("--sample-interval", type=float, default=None)
    parser.add_argument("--actor-domain", choices=("physical", "slot", "client"), default=None)
    parser.add_argument("--key-count", type=int, default=None)
    parser.add_argument("--hot-key-probability", type=float, default=None)
    parser.add_argument("--client-count", type=int, default=None)
    parser.add_argument("--write-interval", type=float, default=None)
    parser.add_argument("--client-think-time", type=float, default=None)
    parser.add_argument("--merge-probability", type=float, default=None)
    parser.add_argument("--burst-interval", type=float, default=None)
    parser.add_argument("--burst-spread", type=float, default=None)
    parser.add_argument("--merge-delay", type=float, default=None)
    parser.add_argument("--same-coordinator-probability", type=float, default=None)
    parser.add_argument("--min-lat", type=float, default=None)
    parser.add_argument("--max-lat", type=float, default=None)
    parser.add_argument("--analysis-window", type=float, default=None)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.jobs < 1:
        raise SystemExit("--jobs must be >= 1")

    config_path = Path(args.config)
    config = load_config(config_path)
    writer_counts = args.writer_counts or [1, 2, 4, 8, 16, 32, 64, 128, 256]
    clocks = args.clocks or list(config_get(config, "clocks", ["vv", "dvv", "lease_dvv"]))
    profiles = args.profiles or ["stable", "sustained", "burst"]
    seeds = args.seeds or list(config_get(config, "seeds", [1, 2, 3]))
    lease_duration = args.lease_duration if args.lease_duration is not None else float(config_get(config, "lease-duration", 16.0))
    lease_durations = (
        [lease_duration]
        if args.fixed_lease_duration
        else (
            args.lease_durations
            if args.lease_durations is not None
            else list(config_get(config, "lease-durations", [8.0, 16.0, 32.0]))
        )
    )
    if not lease_durations:
        lease_durations = [lease_duration]

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = Path(args.output_dir or config_get(config, "output-dir", "output/experiments"))
    experiment_name = args.experiment_name or f"{timestamp}_extreme_fanout_writer_sweep"
    experiment_dir = output_dir / experiment_name
    aggregate_dir = experiment_dir / "aggregate"
    figures_dir = experiment_dir / "figures"
    config_dir = experiment_dir / "config"
    runs_dir = experiment_dir / "runs"
    for path in (aggregate_dir, figures_dir, config_dir, runs_dir):
        path.mkdir(parents=True, exist_ok=True)

    scenario_options = {
        "sim_time": args.sim_time if args.sim_time is not None else float(config_get(config, "sim-time", 180.0)),
        "initial_size": args.initial_size if args.initial_size is not None else int(config_get(config, "initial-size", 12)),
        "max_nodes": args.max_nodes if args.max_nodes is not None else int(config_get(config, "max-nodes", 16)),
        "min_nodes": args.min_nodes if args.min_nodes is not None else int(config_get(config, "min-nodes", 4)),
        "replication_factor": args.replication_factor if args.replication_factor is not None else int(config_get(config, "replication-factor", 8)),
        "sample_interval": args.sample_interval if args.sample_interval is not None else float(config_get(config, "sample-interval", 2.0)),
        "actor_domain": args.actor_domain or str(config_get(config, "actor-domain", "client")),
        "key_count": args.key_count if args.key_count is not None else int(config_get(config, "key-count", 1)),
        "key_distribution": str(config_get(config, "key-distribution", "hotcold")),
        "hot_key_probability": args.hot_key_probability if args.hot_key_probability is not None else float(config_get(config, "hot-key-probability", 1.0)),
        "zipf_skew": float(config_get(config, "zipf-skew", 1.0)),
        "client_count": args.client_count if args.client_count is not None else int(config_get(config, "client-count", 100000)),
        "write_interval": args.write_interval if args.write_interval is not None else float(config_get(config, "write-interval", 1000.0)),
        "client_think_time": args.client_think_time if args.client_think_time is not None else float(config_get(config, "client-think-time", 0.01)),
        "merge_probability": args.merge_probability if args.merge_probability is not None else float(config_get(config, "merge-probability", 1.0)),
        "burst_interval": args.burst_interval if args.burst_interval is not None else float(config_get(config, "burst-interval", 10.0)),
        "burst_spread": args.burst_spread if args.burst_spread is not None else float(config_get(config, "burst-spread", 0.15)),
        "merge_delay": args.merge_delay if args.merge_delay is not None else float(config_get(config, "merge-delay", 8.0)),
        "same_coordinator_probability": (
            args.same_coordinator_probability
            if args.same_coordinator_probability is not None
            else float(config_get(config, "same-coordinator-probability", 0.0))
        ),
        "min_lat": args.min_lat if args.min_lat is not None else float(config_get(config, "min-lat", 0.05)),
        "max_lat": args.max_lat if args.max_lat is not None else float(config_get(config, "max-lat", 0.2)),
    }
    analysis_window = args.analysis_window if args.analysis_window is not None else float(config_get(config, "analysis-window", 6.0))
    experiment_config = {
        "source_config": str(config_path),
        "writer_counts": writer_counts,
        "clocks": clocks,
        "profiles": profiles,
        "seeds": seeds,
        "lease_duration": lease_duration,
        "lease_durations": lease_durations,
        "fixed_lease_duration": args.fixed_lease_duration,
        "analysis_window": analysis_window,
        "jobs": args.jobs,
        "progress": args.progress,
        "write_pdf": args.write_pdf,
        "output_dir": str(output_dir),
        "experiment_name": experiment_name,
        **scenario_options,
    }
    (config_dir / "experiment_config.json").write_text(json.dumps(experiment_config, indent=2))
    if config_path.exists():
        (config_dir / "source_config.yaml").write_text(config_path.read_text())

    run_specs: list[dict[str, object]] = []
    for burst_writers in writer_counts:
        for profile in profiles:
            for clock in clocks:
                clock_lease_durations = lease_durations if is_lease_clock(clock) else [lease_durations[0]]
                for clock_lease_duration in clock_lease_durations:
                    variant = clock_variant(clock, clock_lease_duration, len(clock_lease_durations))
                    for seed in seeds:
                        run_name = f"w{burst_writers:03d}_{profile}_{variant}_seed{seed}"
                        scenario_config = scenario_config_from_kwargs(
                            profile=profile,
                            seed=seed,
                            burst_writers=burst_writers,
                            **scenario_options,
                        )
                        run_specs.append(
                            {
                                "order": len(run_specs),
                                "burst_writers": burst_writers,
                                "profile": profile,
                                "clock": clock,
                                "variant": variant,
                                "lease_duration": clock_lease_duration,
                                "seed": seed,
                                "run_name": run_name,
                                "run_dir": str(runs_dir / run_name),
                                "scenario_config": scenario_config,
                                "experiment_config": experiment_config,
                                "analysis_window": analysis_window,
                            }
                        )

    print("Running extreme fanout writer sweep")
    print(f"  output: {experiment_dir}")
    print(f"  runs: {len(run_specs)}")
    print(f"  writer counts: {writer_counts}")
    print(f"  clocks: {clocks}")
    print(f"  profiles: {profiles}")

    progress_bar = None
    if args.progress:
        from tqdm.auto import tqdm

        progress_bar = tqdm(total=len(run_specs), desc="fanout runs", unit="run")

    run_rows: list[dict[str, object]] = []
    try:
        if args.jobs == 1:
            for spec in run_specs:
                run_rows.append(run_fanout_case(spec))
                if progress_bar is not None:
                    progress_bar.update(1)
        else:
            with ProcessPoolExecutor(max_workers=args.jobs) as executor:
                futures = [executor.submit(run_fanout_case, spec) for spec in run_specs]
                for future in as_completed(futures):
                    run_rows.append(future.result())
                    if progress_bar is not None:
                        progress_bar.update(1)
    finally:
        if progress_bar is not None:
            progress_bar.close()

    run_rows.sort(key=lambda row: int(row.pop("_order")))
    aggregated_rows = aggregate_by_fanout(run_rows)
    exact_rows = exact_summary(aggregated_rows)
    lease_rows = lease_tradeoff_summary(aggregated_rows)

    aggregate_files = [
        "aggregate/comparison_runs.csv",
        "aggregate/comparison_by_fanout.csv",
        "aggregate/fanout_exact_summary.csv",
        "aggregate/lease_fanout_tradeoff.csv",
    ]
    write_csv(aggregate_dir / "comparison_runs.csv", run_rows)
    write_csv(aggregate_dir / "comparison_by_fanout.csv", aggregated_rows)
    write_csv(aggregate_dir / "fanout_exact_summary.csv", exact_rows)
    write_csv(aggregate_dir / "lease_fanout_tradeoff.csv", lease_rows)

    figure_specs = [
        (
            OBJECT_METADATA_KEY,
            "Average sibling-set metadata bytes",
            "Sibling-Set Metadata vs Writer Fanout",
            "sibling_set_metadata_bytes_vs_writers.png",
        ),
        (
            WRITE_STAMP_METADATA_KEY,
            "Average per-write metadata bytes",
            "Per-Write Metadata vs Writer Fanout",
            "write_metadata_bytes_vs_writers.png",
        ),
        (
            ACCURACY_RECALL_KEY,
            "Average ancestry recall",
            "Ancestry Recall vs Writer Fanout",
            "recall_vs_writers.png",
        ),
        (
            STALE_SIBLING_KEY,
            "Stale sibling rate",
            "Stale Sibling Rate vs Writer Fanout",
            "stale_sibling_rate_vs_writers.png",
        ),
        (
            MISSED_CONFLICT_KEY,
            "Missed conflict rate",
            "Missed Conflict Rate vs Writer Fanout",
            "missed_conflict_rate_vs_writers.png",
        ),
        (
            HOT_SIBLINGS_KEY,
            "Average hot-key siblings",
            "Hot-Key Siblings vs Writer Fanout",
            "hot_key_siblings_vs_writers.png",
        ),
    ]
    figure_files: list[str] = []
    for metric_key, ylabel, title, filename in figure_specs:
        plot_metric_by_fanout(
            aggregated_rows,
            metric_key=metric_key,
            ylabel=ylabel,
            title=title,
            output_path=figures_dir / filename,
            write_pdf=args.write_pdf,
        )
        figure_files.append(f"figures/{filename}")
        if args.write_pdf:
            figure_files.append(f"figures/{Path(filename).with_suffix('.pdf').name}")

    plot_exact_reduction(
        exact_rows,
        figures_dir / "dvv_reduction_vs_vv_by_writers.png",
        write_pdf=args.write_pdf,
    )
    figure_files.append("figures/dvv_reduction_vs_vv_by_writers.png")
    if args.write_pdf:
        figure_files.append("figures/dvv_reduction_vs_vv_by_writers.pdf")

    plot_lease_tradeoff(
        lease_rows,
        figures_dir / "lease_fanout_tradeoff.png",
        write_pdf=args.write_pdf,
    )
    figure_files.append("figures/lease_fanout_tradeoff.png")
    if args.write_pdf:
        figure_files.append("figures/lease_fanout_tradeoff.pdf")

    existing_figure_files = [
        path for path in figure_files if (experiment_dir / path).exists()
    ]
    write_report(
        experiment_dir,
        experiment_config=experiment_config,
        aggregate_files=aggregate_files,
        figure_files=existing_figure_files,
    )

    manifest = {
        "experiment_directory": str(experiment_dir.resolve()),
        "experiment_name": experiment_name,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "git_commit": git_commit(),
        "config": experiment_config,
        "aggregate_files": aggregate_files,
        "figure_files": existing_figure_files,
        "report": "study_report.md",
        "run_count": len(run_rows),
    }
    (experiment_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))

    print(f"Report: {experiment_dir / 'study_report.md'}")
    print(f"Main figure: {figures_dir / 'sibling_set_metadata_bytes_vs_writers.png'}")
    print(f"Lease tradeoff: {figures_dir / 'lease_fanout_tradeoff.png'}")


if __name__ == "__main__":
    main()
