"""Command-line interface for the simulator."""

from __future__ import annotations

import json
from pathlib import Path

import configargparse

from .clocks import CLOCK_FACTORIES, make_clock_factory
from .sim import CHURN_PROFILES, run_scenario, save_run


def build_parser() -> configargparse.ArgParser:
    parser = configargparse.ArgParser(
        description="Run the per-object causality simulator.",
        default_config_files=["configs/simulate.yaml"],
        config_file_parser_class=configargparse.YAMLConfigFileParser,
    )
    parser.add("-c", "--config", is_config_file=True, help="Path to a YAML config file.")
    parser.add_argument("--profile", choices=sorted(CHURN_PROFILES), default="sustained")
    parser.add_argument("--clock", choices=sorted(CLOCK_FACTORIES), default="dvv")
    parser.add_argument("--sim-time", type=float, default=240.0)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--initial-size", type=int, default=10)
    parser.add_argument("--write-interval", type=float, default=5.0)
    parser.add_argument("--client-think-time", type=float, default=4.0)
    parser.add_argument("--merge-probability", type=float, default=0.35)
    parser.add_argument("--burst-interval", type=float, default=18.0)
    parser.add_argument("--burst-writers", type=int, default=4)
    parser.add_argument("--burst-spread", type=float, default=2.0)
    parser.add_argument("--merge-delay", type=float, default=10.0)
    parser.add_argument("--same-coordinator-probability", type=float, default=0.75)
    parser.add_argument("--max-nodes", type=int, default=28)
    parser.add_argument("--min-nodes", type=int, default=4)
    parser.add_argument("--min-lat", type=float, default=1.0)
    parser.add_argument("--max-lat", type=float, default=5.0)
    parser.add_argument("--key-count", type=int, default=12)
    parser.add_argument("--hot-key-probability", type=float, default=0.65)
    parser.add_argument("--client-count", type=int, default=128)
    parser.add_argument("--replication-factor", type=int, default=4)
    parser.add_argument("--sample-interval", type=float, default=10.0)
    parser.add_argument("--lease-duration", type=float, default=16.0)
    parser.add_argument("--output-dir", default="output/runs")
    parser.add_argument("--run-name", default="clock_study")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    metrics = run_scenario(
        profile=args.profile,
        clock_factory=make_clock_factory(args.clock, args.lease_duration),
        sim_time=args.sim_time,
        seed=args.seed,
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
        lease_duration=args.lease_duration,
        client_think_time=args.client_think_time,
        merge_probability=args.merge_probability,
        burst_interval=args.burst_interval,
        burst_writers=args.burst_writers,
        burst_spread=args.burst_spread,
        merge_delay=args.merge_delay,
        same_coordinator_probability=args.same_coordinator_probability,
    )
    output_dir = Path(args.output_dir)
    config = {
        "profile": args.profile,
        "clock": args.clock,
        "sim_time": args.sim_time,
        "seed": args.seed,
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
        "lease_duration": args.lease_duration,
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

