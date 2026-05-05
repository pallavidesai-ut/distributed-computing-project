from __future__ import annotations

import pytest

from clocksim import make_clock_factory, run_scenario


def tiny_scenario(clock: str, *, lease_duration: float = 60.0):
    return run_scenario(
        profile="stable",
        clock_factory=make_clock_factory(clock, lease_duration),
        sim_time=40.0,
        seed=123,
        initial_size=4,
        write_interval=2.0,
        max_nodes=4,
        min_nodes=4,
        min_lat=0.1,
        max_lat=0.2,
        key_count=3,
        hot_key_probability=0.7,
        client_count=16,
        replication_factor=3,
        sample_interval=10.0,
        client_think_time=0.1,
        merge_probability=0.7,
        burst_interval=12.0,
        burst_writers=3,
        burst_spread=0.2,
        merge_delay=1.0,
        same_coordinator_probability=0.8,
    )


@pytest.mark.parametrize("clock", ["vv", "dvv", "itc"])
def test_exact_clocks_have_perfect_history_fidelity_in_smoke_run(clock: str) -> None:
    metrics = tiny_scenario(clock)
    summary = metrics.summary(40.0)

    assert summary["total_writes"] > 0
    assert summary["avg_history_precision"] == pytest.approx(1.0)
    assert summary["avg_history_recall"] == pytest.approx(1.0)
    assert all(float(row["precision"]) == pytest.approx(1.0) for row in metrics.accuracy)
    assert all(float(row["recall"]) == pytest.approx(1.0) for row in metrics.accuracy)


def test_aggressive_lease_dvv_prunes_and_loses_some_recall_in_smoke_run() -> None:
    metrics = tiny_scenario("lease_dvv", lease_duration=0.01)
    summary = metrics.summary(40.0)

    assert summary["total_writes"] > 0
    assert summary["pruned_write_rate"] > 0
    assert summary["avg_history_precision"] == pytest.approx(1.0)
    assert summary["avg_history_recall"] < 1.0
