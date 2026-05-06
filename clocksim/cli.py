"""Command-line interface for the simulator."""

from __future__ import annotations

import json
from pathlib import Path

import configargparse

from .clocks import CLOCK_FACTORIES, make_clock_factory
from .config import add_scenario_args, scenario_config_from_args, scenario_config_to_dict
from .sim import run_scenario, save_run


def build_parser() -> configargparse.ArgParser:
    parser = configargparse.ArgParser(
        description="Run the per-object causality simulator.",
        default_config_files=["configs/simulate.yaml"],
        config_file_parser_class=configargparse.YAMLConfigFileParser,
    )
    parser.add("-c", "--config", is_config_file=True, help="Path to a YAML config file.")
    add_scenario_args(parser, include_profile=True, include_seed=True)
    parser.add_argument("--clock", choices=sorted(CLOCK_FACTORIES), default="dvv")
    parser.add_argument("--lease-duration", type=float, default=16.0)
    parser.add_argument("--progress", action="store_true", help="Show tqdm progress over simulation time.")
    parser.add_argument("--output-dir", default="output/runs")
    parser.add_argument("--run-name", default="clock_study")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    scenario_config = scenario_config_from_args(args)
    metrics = run_scenario(
        config=scenario_config,
        clock_factory=make_clock_factory(args.clock, args.lease_duration),
        progress=args.progress,
        progress_label=args.run_name,
    )
    output_dir = Path(args.output_dir)
    config = {
        **scenario_config_to_dict(scenario_config),
        "clock": args.clock,
        "lease_duration": args.lease_duration,
        "progress": args.progress,
        "output_dir": args.output_dir,
        "run_name": args.run_name,
    }
    summary = save_run(
        metrics,
        output_dir=output_dir,
        run_name=args.run_name,
        config=config,
        sim_time=args.sim_time,
    )
    print(json.dumps(summary, indent=2))

