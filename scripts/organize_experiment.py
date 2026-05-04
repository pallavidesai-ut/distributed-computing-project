#!/usr/bin/env python3
"""Organize a completed experiment directory into paper-friendly subfolders."""

from __future__ import annotations

import csv
import json
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


AGGREGATE_CSVS = {
    "comparison_runs.csv",
    "comparison_by_clock.csv",
    "lease_duration_ablation.csv",
    "metadata_reduction_vs_recall_loss.csv",
    "same_replica_concurrency_example.csv",
    "headline_results.csv",
    "failure_modes_by_clock.csv",
}

ROOT_DOCS = {
    "study_report.md",
    "plot_interpretation.md",
}


def git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return "unknown"


def move_file(path: Path, dest_dir: Path) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / path.name
    if path.resolve() == dest.resolve():
        return dest
    if dest.exists():
        dest.unlink()
    shutil.move(str(path), str(dest))
    return dest


def move_dir(path: Path, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if path.resolve() == dest.resolve():
        return dest
    if dest.exists():
        shutil.rmtree(dest)
    shutil.move(str(path), str(dest))
    return dest


def rewrite_comparison_runs(path: Path, experiment_dir: Path) -> None:
    if not path.exists():
        return
    with path.open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        return

    fields = list(rows[0].keys())
    for row in rows:
        run_name = row.get("run_name")
        if not run_name:
            continue
        run_dir = experiment_dir / "runs" / run_name
        row["run_dir"] = str(run_dir)
        row["analysis_dir"] = str(run_dir / "analysis")

    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def organize_run_dir(run_dir: Path) -> None:
    run_name = run_dir.name
    raw_dir = run_dir / "raw"
    analysis_dir = run_dir / "analysis"

    # Move raw run CSVs into raw/.
    for file_path in list(run_dir.glob(f"{run_name}_*.csv")):
        move_file(file_path, raw_dir)

    # Keep summary/config JSON at run root for quick inspection.

    # Rename <run>_analysis/ to analysis/ and flatten its contents.
    old_analysis = run_dir / f"{run_name}_analysis"
    if old_analysis.exists() and old_analysis.is_dir():
        if analysis_dir.exists():
            for child in old_analysis.iterdir():
                move_file(child, analysis_dir) if child.is_file() else move_dir(child, analysis_dir / child.name)
            old_analysis.rmdir()
        else:
            move_dir(old_analysis, analysis_dir)


def list_relative_files(base: Path, subdir: str) -> list[str]:
    target = base / subdir
    if not target.exists():
        return []
    return sorted(str(path.relative_to(base)) for path in target.rglob("*") if path.is_file())


def create_manifest(experiment_dir: Path) -> None:
    config_path = experiment_dir / "config" / "experiment_config.json"
    config: dict[str, Any] = {}
    if config_path.exists():
        try:
            config = json.loads(config_path.read_text())
        except json.JSONDecodeError:
            config = {}

    manifest = {
        "experiment_directory": str(experiment_dir.resolve()),
        "experiment_name": experiment_dir.name,
        "organized_at": datetime.now().isoformat(timespec="seconds"),
        "git_commit": git_commit(),
        "config": config,
        "aggregate_files": list_relative_files(experiment_dir, "aggregate"),
        "figure_files": list_relative_files(experiment_dir, "figures"),
        "time_series_files": list_relative_files(experiment_dir, "time_series"),
        "run_count": len([path for path in (experiment_dir / "runs").iterdir()])
        if (experiment_dir / "runs").exists()
        else 0,
    }
    (experiment_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))


def create_readme(experiment_dir: Path) -> None:
    readme = experiment_dir / "README.md"
    if readme.exists():
        return
    readme.write_text(
        "\n".join(
            [
                f"# Experiment `{experiment_dir.name}`",
                "",
                "This directory has been organized for browsing and paper analysis.",
                "",
                "## Structure",
                "",
                "- `manifest.json`: reproducibility metadata and artifact inventory.",
                "- `config/`: source and resolved experiment configuration.",
                "- `aggregate/`: cross-run CSV tables.",
                "- `figures/`: top-level aggregate plots.",
                "- `time_series/`: aggregate time-series CSVs and plots.",
                "- `runs/`: one directory per profile/clock/seed run.",
                "  - `raw/`: raw per-run CSVs.",
                "  - `analysis/`: per-run analysis CSVs and plots.",
                "",
                "Start with `aggregate/comparison_by_clock.csv`, `figures/`, and `time_series/` for paper-level interpretation.",
                "",
            ]
        )
    )


def organize_experiment(experiment_dir: Path) -> None:
    if not experiment_dir.exists() or not experiment_dir.is_dir():
        raise SystemExit(f"Experiment directory not found: {experiment_dir}")

    aggregate_dir = experiment_dir / "aggregate"
    figures_dir = experiment_dir / "figures"
    config_dir = experiment_dir / "config"
    runs_dir = experiment_dir / "runs"

    # Top-level aggregate tables.
    for file_path in list(experiment_dir.iterdir()):
        if file_path.is_file() and file_path.name in AGGREGATE_CSVS:
            move_file(file_path, aggregate_dir)

    # Top-level plots.
    for pattern in ("*.png", "*.pdf"):
        for file_path in list(experiment_dir.glob(pattern)):
            move_file(file_path, figures_dir)

    # Source/resolved experiment configs.
    config_file = experiment_dir / "experiment_config.json"
    if config_file.exists():
        move_file(config_file, config_dir)
    for file_path in list(experiment_dir.glob("*.yaml")):
        move_file(file_path, config_dir)

    # Aggregate time-series report.
    old_ts = experiment_dir / "time_series_report"
    new_ts = experiment_dir / "time_series"
    if old_ts.exists() and old_ts.is_dir():
        move_dir(old_ts, new_ts)

    # Per-run directories.
    ignored_dirs = {"aggregate", "figures", "config", "time_series", "runs"}
    for child in list(experiment_dir.iterdir()):
        if not child.is_dir() or child.name in ignored_dirs:
            continue
        moved = move_dir(child, runs_dir / child.name)
        organize_run_dir(moved)

    # If already organized, still normalize existing run dirs.
    if runs_dir.exists():
        for child in runs_dir.iterdir():
            if child.is_dir():
                organize_run_dir(child)

    comparison_runs = aggregate_dir / "comparison_runs.csv"
    rewrite_comparison_runs(comparison_runs, experiment_dir)

    create_manifest(experiment_dir)
    create_readme(experiment_dir)


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: scripts/organize_experiment.py <experiment_dir>")
    organize_experiment(Path(sys.argv[1]))


if __name__ == "__main__":
    main()
