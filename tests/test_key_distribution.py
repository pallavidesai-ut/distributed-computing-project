"""Key distribution smoke tests."""

from __future__ import annotations

import random

from clocksim import (
    Cluster,
    DottedVersionVectorModel,
    Environment,
    MetricsCollector,
    WorkloadConfig,
)


def build_cluster(workload: WorkloadConfig) -> Cluster:
    return Cluster(
        env=Environment(),
        metrics=MetricsCollector(),
        clock_model=DottedVersionVectorModel(),
        initial_size=1,
        max_nodes=1,
        min_nodes=1,
        min_lat=0.0,
        max_lat=0.0,
        sample_interval=10.0,
        actor_domain="client",
        workload=workload,
    )


def test_zipf_key_distribution_skews_hot_key() -> None:
    random.seed(123)
    cluster = build_cluster(
        WorkloadConfig(
            key_count=12,
            key_distribution="zipf",
            zipf_skew=1.1,
            write_interval=100.0,
            client_count=4,
            client_think_time=1.0,
            merge_probability=0.0,
            burst_interval=100.0,
            burst_writers=1,
            burst_spread=0.0,
            merge_delay=1.0,
            same_coordinator_probability=1.0,
            replication_factor=1,
        )
    )

    draws = [cluster.choose_key() for _ in range(5000)]
    hot_ratio = draws.count("k0") / len(draws)
    all_keys = {f"k{i}" for i in range(cluster.workload.key_count)}

    assert hot_ratio > 0.32
    assert hot_ratio < 1.0
    assert set(draws).issubset(all_keys)


def test_hotcold_distribution_uses_hot_key_probability() -> None:
    random.seed(999)
    cluster = build_cluster(
        WorkloadConfig(
            key_count=12,
            key_distribution="hotcold",
            hot_key_probability=1.0,
            write_interval=100.0,
            client_count=4,
            client_think_time=1.0,
            merge_probability=0.0,
            burst_interval=100.0,
            burst_writers=1,
            burst_spread=0.0,
            merge_delay=1.0,
            same_coordinator_probability=1.0,
            replication_factor=1,
        )
    )

    assert all(cluster.choose_key() == "k0" for _ in range(100))
