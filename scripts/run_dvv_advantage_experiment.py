#!/usr/bin/env python3
"""Run a deterministic experiment that isolates DVV sibling-set metadata wins."""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from clocksim import (
    CausalContext,
    DottedVersionVectorModel,
    VersionVectorModel,
    repeated_stamp_set_encoding,
    shared_dvv_set_encoding,
)
from clocksim.clocks import BaseStamp, ClockModel


def json_bytes(payload: dict[str, Any]) -> int:
    return len(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def pct_reduction(baseline: float, candidate: float) -> float:
    if baseline <= 0.0:
        return 0.0
    return round((1.0 - candidate / baseline) * 100.0, 3)


def git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return "unknown"


def issue_concurrent_stamps(
    model: ClockModel,
    base_context: CausalContext,
    sibling_count: int,
) -> list[BaseStamp]:
    state = model.make_state("coordinator")
    return [
        model.issue_stamp(
            state,
            "k0",
            base_context.clone(),
            now=0.0,
            actor_id=f"w{index:04d}",
        )
        for index in range(1, sibling_count + 1)
    ]


def run_case(
    *,
    ancestor_actors: int,
    ancestor_counter: int,
    sibling_count: int,
) -> dict[str, Any]:
    base_context = CausalContext(
        prefix={
            f"a{index:04d}": ancestor_counter
            for index in range(1, ancestor_actors + 1)
        }
    )

    vv_stamps = issue_concurrent_stamps(
        VersionVectorModel(),
        base_context,
        sibling_count,
    )
    dvv_stamps = issue_concurrent_stamps(
        DottedVersionVectorModel(),
        base_context,
        sibling_count,
    )

    vv_repeated = repeated_stamp_set_encoding(vv_stamps)
    dvv_shared = shared_dvv_set_encoding(dvv_stamps)
    reconstructed = dvv_shared.reconstructed_contexts()
    original = [stamp.represented_context() for stamp in dvv_stamps]
    exact_reconstruction = all(
        left.prefix == right.prefix and left.dots == right.dots
        for left, right in zip(reconstructed, original, strict=True)
    )

    return {
        "ancestor_actors": ancestor_actors,
        "ancestor_counter": ancestor_counter,
        "sibling_count": sibling_count,
        "vv_repeated_set_bytes": vv_repeated.metadata_bytes(),
        "shared_dvv_set_bytes": dvv_shared.metadata_bytes(),
        "vv_repeated_components": vv_repeated.metadata_component_count(),
        "shared_dvv_components": dvv_shared.metadata_component_count(),
        "shared_dvv_vs_vv_set_reduction_pct": pct_reduction(
            vv_repeated.metadata_bytes(),
            dvv_shared.metadata_bytes(),
        ),
        "exact_reconstruction": int(exact_reconstruction),
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("")
        return
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def make_plots(rows: list[dict[str, Any]], figures_dir: Path, *, write_pdf: bool) -> list[str]:
    figures_dir.mkdir(parents=True, exist_ok=True)
    import matplotlib.pyplot as plt

    artifacts: list[str] = []
    widths = sorted({int(row["ancestor_actors"]) for row in rows})

    fig, ax = plt.subplots(figsize=(8, 5))
    for width in widths:
        width_rows = sorted(
            [row for row in rows if int(row["ancestor_actors"]) == width],
            key=lambda row: int(row["sibling_count"]),
        )
        ax.plot(
            [int(row["sibling_count"]) for row in width_rows],
            [float(row["shared_dvv_vs_vv_set_reduction_pct"]) for row in width_rows],
            marker="o",
            label=f"{width} ancestors",
        )
    ax.axhline(0.0, color="#555555", linewidth=0.8)
    ax.set_xscale("log", base=2)
    ax.set_xlabel("Concurrent siblings sharing one ancestor context")
    ax.set_ylabel("Shared DVV reduction vs repeated VV (%)")
    ax.set_title("DVV Shared-Context Advantage")
    ax.legend()
    fig.tight_layout()
    path = figures_dir / "shared_dvv_reduction_vs_siblings.png"
    fig.savefig(path, dpi=160)
    artifacts.append(str(path))
    if write_pdf:
        pdf_path = path.with_suffix(".pdf")
        fig.savefig(pdf_path)
        artifacts.append(str(pdf_path))
    plt.close(fig)

    widest = max(widths)
    width_rows = sorted(
        [row for row in rows if int(row["ancestor_actors"]) == widest],
        key=lambda row: int(row["sibling_count"]),
    )
    fig, ax = plt.subplots(figsize=(8, 5))
    ax.plot(
        [int(row["sibling_count"]) for row in width_rows],
        [int(row["vv_repeated_set_bytes"]) for row in width_rows],
        marker="o",
        label="VV repeated vectors",
    )
    ax.plot(
        [int(row["sibling_count"]) for row in width_rows],
        [int(row["shared_dvv_set_bytes"]) for row in width_rows],
        marker="o",
        label="DVV shared summary",
    )
    ax.set_xscale("log", base=2)
    ax.set_xlabel("Concurrent siblings")
    ax.set_ylabel("Serialized metadata bytes")
    ax.set_title(f"Sibling-Set Metadata at {widest} Ancestor Actors")
    ax.legend()
    fig.tight_layout()
    path = figures_dir / "sibling_set_bytes_widest_context.png"
    fig.savefig(path, dpi=160)
    artifacts.append(str(path))
    if write_pdf:
        pdf_path = path.with_suffix(".pdf")
        fig.savefig(pdf_path)
        artifacts.append(str(pdf_path))
    plt.close(fig)

    return artifacts


def build_report(
    rows: list[dict[str, Any]],
    *,
    config: dict[str, Any],
    experiment_dir: Path,
    figures: list[str],
) -> None:
    best = max(rows, key=lambda row: float(row["shared_dvv_vs_vv_set_reduction_pct"]))

    break_even: list[str] = []
    for width in sorted({int(row["ancestor_actors"]) for row in rows}):
        actor_label = "ancestor actor" if width == 1 else "ancestor actors"
        width_rows = sorted(
            [row for row in rows if int(row["ancestor_actors"]) == width],
            key=lambda row: int(row["sibling_count"]),
        )
        first = next(
            (
                row
                for row in width_rows
                if float(row["shared_dvv_vs_vv_set_reduction_pct"]) > 0.0
            ),
            None,
        )
        if first is None:
            break_even.append(f"- {width} {actor_label}: no positive reduction in this grid")
        else:
            break_even.append(
                f"- {width} {actor_label}: {first['sibling_count']} siblings "
                f"({first['shared_dvv_vs_vv_set_reduction_pct']}% reduction)"
            )

    figure_lines = [f"- `{Path(path).relative_to(experiment_dir)}`" for path in figures]
    report = [
        "# DVV Shared-Context Advantage Experiment",
        "",
        "This deterministic experiment compares VV and DVV using a shared-context DVV sibling-set representation.",
        "",
        "The experiment models many concurrent siblings with identical ancestor context and measures metadata size at the set encoding level.",
        "",
        "## Headline",
        "",
        f"- Best shared DVV reduction vs repeated VV: {best['shared_dvv_vs_vv_set_reduction_pct']}% at {best['ancestor_actors']} ancestor actors and {best['sibling_count']} siblings.",
        f"- All shared DVV encodings reconstruct the original per-version contexts exactly: {all(int(row['exact_reconstruction']) for row in rows)}.",
        "",
        "## Break-Even Points",
        "",
        *break_even,
        "",
        "## Output Files",
        "",
        "- `config/experiment_config.json`: resolved experiment knobs.",
        "- `aggregate/dvv_advantage_grid.csv`: full grid of byte/component measurements.",
        *figure_lines,
        "",
        "## Interpretation",
        "",
        "- VV repeats an entire vector for each sibling.",
        "- DVV shared-summary encodings store common ancestry once plus one dot per sibling.",
        "- Exact DVV preserves full ancestry semantics over the chosen actor domain; the improvement shown here is from shared metadata layout.",
        "",
        "## Config",
        "",
        "```json",
        json.dumps(config, indent=2),
        "```",
        "",
    ]
    (experiment_dir / "study_report.md").write_text("\n".join(report))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compare VV with shared-summary DVV sibling-set encodings."
    )
    parser.add_argument("--ancestor-actors", nargs="+", type=int, default=[1, 4, 16, 64, 128])
    parser.add_argument("--sibling-counts", nargs="+", type=int, default=[1, 2, 4, 8, 16, 32, 64])
    parser.add_argument("--ancestor-counter", type=int, default=1)
    parser.add_argument("--output-dir", default="output/experiments")
    parser.add_argument("--experiment-name", default="dvv_shared_context_advantage")
    parser.add_argument("--write-pdf", action="store_true")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    experiment_dir = Path(args.output_dir) / f"{timestamp}_{args.experiment_name}"
    aggregate_dir = experiment_dir / "aggregate"
    figures_dir = experiment_dir / "figures"
    config_dir = experiment_dir / "config"
    config_dir.mkdir(parents=True, exist_ok=True)

    config = {
        "ancestor_actors": args.ancestor_actors,
        "sibling_counts": args.sibling_counts,
        "ancestor_counter": args.ancestor_counter,
        "output_dir": args.output_dir,
        "experiment_name": args.experiment_name,
        "write_pdf": args.write_pdf,
    }
    (config_dir / "experiment_config.json").write_text(json.dumps(config, indent=2))

    rows = [
        run_case(
            ancestor_actors=ancestor_actors,
            ancestor_counter=args.ancestor_counter,
            sibling_count=sibling_count,
        )
        for ancestor_actors in args.ancestor_actors
        for sibling_count in args.sibling_counts
    ]
    write_csv(aggregate_dir / "dvv_advantage_grid.csv", rows)
    figures = make_plots(rows, figures_dir, write_pdf=args.write_pdf)
    build_report(rows, config=config, experiment_dir=experiment_dir, figures=figures)

    manifest = {
        "experiment_directory": str(experiment_dir.resolve()),
        "experiment_name": experiment_dir.name,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "git_commit": git_commit(),
        "config": config,
        "aggregate_files": ["aggregate/dvv_advantage_grid.csv"],
        "figure_files": [str(Path(path).relative_to(experiment_dir)) for path in figures],
        "report": "study_report.md",
    }
    (experiment_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))

    print(f"Experiment: {experiment_dir}")
    print(f"CSV: {aggregate_dir / 'dvv_advantage_grid.csv'}")
    print(f"Report: {experiment_dir / 'study_report.md'}")


if __name__ == "__main__":
    main()
