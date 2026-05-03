#!/usr/bin/env python3
"""Build cross-experiment sensitivity tables/plots from organized runs."""

from __future__ import annotations

import csv
import re
import sys
from pathlib import Path

import matplotlib.pyplot as plt


def read_rows(experiment_dir: Path) -> list[dict[str, str]]:
    path = experiment_dir / "aggregate" / "comparison_by_clock.csv"
    if not path.exists():
        raise SystemExit(f"Missing aggregate table: {path}")
    with path.open(newline="") as handle:
        return list(csv.DictReader(handle))


def detect_dimension(path: Path) -> tuple[str, int] | None:
    name = path.name
    match = re.search(r"client_count_(\d+)", name)
    if match:
        return "client_count", int(match.group(1))
    match = re.search(r"rf(\d+)", name)
    if match:
        return "replication_factor", int(match.group(1))
    return None


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    if not rows:
        return
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def make_plot(rows: list[dict[str, object]], dimension: str, output_dir: Path) -> None:
    subset = [row for row in rows if row["dimension"] == dimension]
    if not subset:
        return
    profiles = sorted({str(row["profile"]) for row in subset})
    clocks = [clock for clock in ["vv", "dvv", "lease_dvv_L16", "vv_vnode"] if any(row["clock"] == clock for row in subset)]
    values = sorted({int(row["value"]) for row in subset})
    fig, axes = plt.subplots(1, len(profiles), figsize=(5 * len(profiles), 4), sharey=True)
    if len(profiles) == 1:
        axes = [axes]
    for axis, profile in zip(axes, profiles):
        for clock in clocks:
            points = [row for row in subset if row["profile"] == profile and row["clock"] == clock]
            by_value = {int(row["value"]): float(row["avg_metadata_bytes"]) for row in points}
            axis.plot(values, [by_value.get(value, float("nan")) for value in values], marker="o", label=clock)
        axis.set_title(profile)
        axis.set_xlabel(dimension.replace("_", " "))
        axis.grid(alpha=0.25)
    axes[0].set_ylabel("Average metadata bytes")
    axes[0].legend()
    fig.suptitle(f"Metadata sensitivity: {dimension.replace('_', ' ')}")
    fig.tight_layout()
    output_dir.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_dir / f"metadata_vs_{dimension}.png", dpi=180)
    plt.close(fig)


def main() -> None:
    if len(sys.argv) < 3:
        raise SystemExit("Usage: scripts/summarize_sensitivity.py OUTPUT_DIR EXPERIMENT_DIR [EXPERIMENT_DIR ...]")
    output_dir = Path(sys.argv[1])
    rows: list[dict[str, object]] = []
    for arg in sys.argv[2:]:
        exp = Path(arg)
        detected = detect_dimension(exp)
        if detected is None:
            continue
        dimension, value = detected
        for row in read_rows(exp):
            rows.append(
                {
                    "experiment": exp.name,
                    "dimension": dimension,
                    "value": value,
                    "profile": row["profile"],
                    "clock": row["clock"],
                    "avg_metadata_bytes": row.get("metadata.avg_metadata_bytes", 0.0),
                    "avg_recall": row.get("accuracy.avg_recall", 0.0),
                    "missed_conflict_rate": row.get("decision_quality.missed_conflict_rate", 0.0),
                    "stale_sibling_rate": row.get("decision_quality.stale_sibling_rate", 0.0),
                }
            )
    write_csv(output_dir / "sensitivity_summary.csv", rows)
    make_plot(rows, "client_count", output_dir)
    make_plot(rows, "replication_factor", output_dir)


if __name__ == "__main__":
    main()
