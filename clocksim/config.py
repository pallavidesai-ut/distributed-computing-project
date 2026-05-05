"""Shared simulator configuration dataclasses and CLI helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import configargparse


@dataclass(frozen=True)
class ChurnProfile:
    join_rate: float = 0.0
    leave_rate: float = 0.0
    burst_size: int = 0
    burst_interval: float | None = None


CHURN_PROFILES: dict[str, ChurnProfile] = {
    "stable": ChurnProfile(),
    "low": ChurnProfile(join_rate=0.01, leave_rate=0.01),
    "sustained": ChurnProfile(join_rate=0.035, leave_rate=0.035),
    "burst": ChurnProfile(join_rate=0.01, leave_rate=0.01, burst_size=6, burst_interval=45.0),
}


@dataclass(frozen=True)
class WorkloadConfig:
    key_count: int = 12
    key_distribution: str = "hotcold"
    hot_key_probability: float = 0.65
    zipf_skew: float = 1.0
    client_count: int = 128
    write_interval: float = 5.0
    client_think_time: float = 4.0
    merge_probability: float = 0.35
    burst_interval: float = 18.0
    burst_writers: int = 4
    burst_spread: float = 2.0
    merge_delay: float = 10.0
    same_coordinator_probability: float = 0.75
    # Deprecated compatibility field; new code uses ClusterConfig.replication_factor.
    replication_factor: int = 4


@dataclass(frozen=True)
class ClusterConfig:
    initial_size: int = 10
    max_nodes: int = 28
    min_nodes: int = 4
    replication_factor: int = 4
    sample_interval: float = 10.0


@dataclass(frozen=True)
class NetworkConfig:
    min_lat: float = 1.0
    max_lat: float = 5.0


ACTOR_DOMAINS = ("physical", "slot", "client")
KEY_DISTRIBUTIONS = ("hotcold", "zipf")


@dataclass(frozen=True)
class ScenarioConfig:
    profile: str = "sustained"
    sim_time: float = 240.0
    seed: int = 42
    actor_domain: str = "physical"
    cluster: ClusterConfig = field(default_factory=ClusterConfig)
    workload: WorkloadConfig = field(default_factory=WorkloadConfig)
    network: NetworkConfig = field(default_factory=NetworkConfig)


SCENARIO_ARG_NAMES = (
    "sim_time",
    "initial_size",
    "write_interval",
    "client_think_time",
    "merge_probability",
    "burst_interval",
    "burst_writers",
    "burst_spread",
    "merge_delay",
    "same_coordinator_probability",
    "max_nodes",
    "min_nodes",
    "min_lat",
    "max_lat",
    "key_count",
    "key_distribution",
    "hot_key_probability",
    "zipf_skew",
    "client_count",
    "replication_factor",
    "sample_interval",
    "actor_domain",
)


def add_scenario_args(
    parser: configargparse.ArgParser,
    *,
    include_profile: bool = False,
    include_seed: bool = False,
) -> None:
    """Add the common single-scenario knobs to a CLI parser."""

    if include_profile:
        parser.add_argument("--profile", choices=sorted(CHURN_PROFILES), default=ScenarioConfig.profile)
    parser.add_argument("--sim-time", type=float, default=ScenarioConfig.sim_time)
    if include_seed:
        parser.add_argument("--seed", type=int, default=ScenarioConfig.seed)
    parser.add_argument("--initial-size", type=int, default=ClusterConfig.initial_size)
    parser.add_argument("--write-interval", type=float, default=WorkloadConfig.write_interval)
    parser.add_argument("--client-think-time", type=float, default=WorkloadConfig.client_think_time)
    parser.add_argument("--merge-probability", type=float, default=WorkloadConfig.merge_probability)
    parser.add_argument("--burst-interval", type=float, default=WorkloadConfig.burst_interval)
    parser.add_argument("--burst-writers", type=int, default=WorkloadConfig.burst_writers)
    parser.add_argument("--burst-spread", type=float, default=WorkloadConfig.burst_spread)
    parser.add_argument("--merge-delay", type=float, default=WorkloadConfig.merge_delay)
    parser.add_argument(
        "--same-coordinator-probability",
        type=float,
        default=WorkloadConfig.same_coordinator_probability,
    )
    parser.add_argument("--max-nodes", type=int, default=ClusterConfig.max_nodes)
    parser.add_argument("--min-nodes", type=int, default=ClusterConfig.min_nodes)
    parser.add_argument("--min-lat", type=float, default=NetworkConfig.min_lat)
    parser.add_argument("--max-lat", type=float, default=NetworkConfig.max_lat)
    parser.add_argument("--key-count", type=int, default=WorkloadConfig.key_count)
    parser.add_argument(
        "--key-distribution",
        choices=KEY_DISTRIBUTIONS,
        default=WorkloadConfig.key_distribution,
        help="Background key-access model: hotcold (Bernoulli hot key) or zipf.",
    )
    parser.add_argument("--hot-key-probability", type=float, default=WorkloadConfig.hot_key_probability)
    parser.add_argument("--zipf-skew", type=float, default=WorkloadConfig.zipf_skew)
    parser.add_argument("--client-count", type=int, default=WorkloadConfig.client_count)
    parser.add_argument("--replication-factor", type=int, default=ClusterConfig.replication_factor)
    parser.add_argument("--sample-interval", type=float, default=ClusterConfig.sample_interval)
    parser.add_argument(
        "--actor-domain",
        choices=ACTOR_DOMAINS,
        default=ScenarioConfig.actor_domain,
        help="Causal actor identity used by clocks: physical node, stable slot/vnode, or client/session.",
    )


def scenario_options_from_args(args: Any) -> dict[str, Any]:
    """Return flat common scenario options from an argparse namespace."""

    return {name: getattr(args, name) for name in SCENARIO_ARG_NAMES}


def scenario_config_from_args(
    args: Any,
    *,
    profile: str | None = None,
    seed: int | None = None,
) -> ScenarioConfig:
    return scenario_config_from_kwargs(
        profile=profile if profile is not None else getattr(args, "profile"),
        seed=seed if seed is not None else getattr(args, "seed"),
        **scenario_options_from_args(args),
    )


def scenario_config_from_kwargs(**kwargs: Any) -> ScenarioConfig:
    """Build a ScenarioConfig from the legacy flat keyword shape."""

    profile = str(kwargs.pop("profile", ScenarioConfig.profile))
    sim_time = float(kwargs.pop("sim_time", ScenarioConfig.sim_time))
    seed = int(kwargs.pop("seed", ScenarioConfig.seed))
    actor_domain = str(kwargs.pop("actor_domain", ScenarioConfig.actor_domain))
    if actor_domain not in ACTOR_DOMAINS:
        raise ValueError(f"Unknown actor_domain {actor_domain!r}; expected one of {ACTOR_DOMAINS}")
    key_distribution = str(
        kwargs.pop("key_distribution", WorkloadConfig.key_distribution)
    )
    if key_distribution not in KEY_DISTRIBUTIONS:
        raise ValueError(
            f"Unknown key_distribution {key_distribution!r}; expected one of {KEY_DISTRIBUTIONS}"
        )
    return ScenarioConfig(
        profile=profile,
        sim_time=sim_time,
        seed=seed,
        actor_domain=actor_domain,
        cluster=ClusterConfig(
            initial_size=int(kwargs.pop("initial_size", ClusterConfig.initial_size)),
            max_nodes=int(kwargs.pop("max_nodes", ClusterConfig.max_nodes)),
            min_nodes=int(kwargs.pop("min_nodes", ClusterConfig.min_nodes)),
            replication_factor=int(kwargs.pop("replication_factor", ClusterConfig.replication_factor)),
            sample_interval=float(kwargs.pop("sample_interval", ClusterConfig.sample_interval)),
        ),
        workload=WorkloadConfig(
            key_count=int(kwargs.pop("key_count", WorkloadConfig.key_count)),
            key_distribution=key_distribution,
            zipf_skew=float(kwargs.pop("zipf_skew", WorkloadConfig.zipf_skew)),
            hot_key_probability=float(kwargs.pop("hot_key_probability", WorkloadConfig.hot_key_probability)),
            client_count=int(kwargs.pop("client_count", WorkloadConfig.client_count)),
            write_interval=float(kwargs.pop("write_interval", WorkloadConfig.write_interval)),
            client_think_time=float(kwargs.pop("client_think_time", WorkloadConfig.client_think_time)),
            merge_probability=float(kwargs.pop("merge_probability", WorkloadConfig.merge_probability)),
            burst_interval=float(kwargs.pop("burst_interval", WorkloadConfig.burst_interval)),
            burst_writers=int(kwargs.pop("burst_writers", WorkloadConfig.burst_writers)),
            burst_spread=float(kwargs.pop("burst_spread", WorkloadConfig.burst_spread)),
            merge_delay=float(kwargs.pop("merge_delay", WorkloadConfig.merge_delay)),
            same_coordinator_probability=float(
                kwargs.pop(
                    "same_coordinator_probability",
                    WorkloadConfig.same_coordinator_probability,
                )
            ),
        ),
        network=NetworkConfig(
            min_lat=float(kwargs.pop("min_lat", NetworkConfig.min_lat)),
            max_lat=float(kwargs.pop("max_lat", NetworkConfig.max_lat)),
        ),
    )


def scenario_config_to_dict(config: ScenarioConfig) -> dict[str, Any]:
    """Serialize a ScenarioConfig in the existing flat JSON/config shape."""

    return {
        "profile": config.profile,
        "sim_time": config.sim_time,
        "seed": config.seed,
        "actor_domain": config.actor_domain,
        "initial_size": config.cluster.initial_size,
        "write_interval": config.workload.write_interval,
        "client_think_time": config.workload.client_think_time,
        "merge_probability": config.workload.merge_probability,
        "burst_interval": config.workload.burst_interval,
        "burst_writers": config.workload.burst_writers,
        "burst_spread": config.workload.burst_spread,
        "merge_delay": config.workload.merge_delay,
        "same_coordinator_probability": config.workload.same_coordinator_probability,
        "max_nodes": config.cluster.max_nodes,
        "min_nodes": config.cluster.min_nodes,
        "min_lat": config.network.min_lat,
        "max_lat": config.network.max_lat,
        "key_count": config.workload.key_count,
        "key_distribution": config.workload.key_distribution,
        "hot_key_probability": config.workload.hot_key_probability,
        "zipf_skew": config.workload.zipf_skew,
        "client_count": config.workload.client_count,
        "replication_factor": config.cluster.replication_factor,
        "sample_interval": config.cluster.sample_interval,
    }
