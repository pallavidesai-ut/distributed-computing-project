from __future__ import annotations

from clocksim import (
    CHURN_PROFILES,
    Cluster,
    DottedVersionVectorModel,
    Environment,
    MetricsCollector,
    WorkloadConfig,
    compare_true_histories,
)


def make_cluster() -> Cluster:
    env = Environment()
    metrics = MetricsCollector()
    workload = WorkloadConfig(
        key_count=2,
        hot_key_probability=1.0,
        client_count=8,
        write_interval=1000.0,
        client_think_time=0.0,
        merge_probability=0.0,
        burst_interval=1000.0,
        burst_writers=2,
        burst_spread=0.0,
        merge_delay=0.0,
        same_coordinator_probability=1.0,
        replication_factor=1,
    )
    assert "stable" in CHURN_PROFILES
    return Cluster(
        env=env,
        metrics=metrics,
        clock_model=DottedVersionVectorModel(),
        profile="stable",
        initial_size=1,
        max_nodes=1,
        min_nodes=1,
        min_lat=0.0,
        max_lat=0.0,
        sample_interval=1000.0,
        actor_domain="client",
        workload=workload,
    )


def test_ramp_peak_churn_profile_rises_then_falls() -> None:
    profile = CHURN_PROFILES["ramp_peak"]
    start_join, start_leave = profile.rates_at(0.0, 100.0)
    peak_join, peak_leave = profile.rates_at(50.0, 100.0)
    end_join, end_leave = profile.rates_at(100.0, 100.0)

    assert start_join < peak_join
    assert end_join < peak_join
    assert start_leave < peak_leave
    assert end_leave < peak_leave
    assert start_join == end_join
    assert start_leave == end_leave


def write(cluster: Cluster, key: str, context=None, actor_id="client"):
    node = cluster.active_nodes()[0]
    cluster.execute_write(
        key=key,
        coordinator=node,
        context_versions=list(context or []),
        phase="test",
        actor_id=actor_id,
    )
    return node.read(key)[-1]


def test_causality_is_per_object_not_global() -> None:
    cluster = make_cluster()

    k0_version = write(cluster, "k0", actor_id="client-a")
    k1_version = write(cluster, "k1", actor_id="client-b")

    assert not any(
        event.key == k0_version.key
        and event.actor == k0_version.dot.actor
        and event.counter == k0_version.dot.counter
        for event in k1_version.true_history
    )
    represented_event_ids = {
        (k1_version.key, dot.actor, dot.counter)
        for dot in k1_version.stamp.represented_context().materialize()
    }
    assert (k0_version.key, k0_version.dot.actor, k0_version.dot.counter) not in represented_event_ids
    assert {(event.key, event.actor, event.counter) for event in k1_version.true_history} == {
        (k1_version.key, k1_version.dot.actor, k1_version.dot.counter)
    }


def test_read_then_write_to_same_object_dominates_previous_version() -> None:
    cluster = make_cluster()
    node = cluster.active_nodes()[0]

    first = write(cluster, "k0", actor_id="client-a")
    second = write(cluster, "k0", context=node.read("k0"), actor_id="client-b")

    assert compare_true_histories(second, first) == "dominates"
    assert second.stamp.represented_context().contains(first.dot)
    assert node.read("k0") == [second]


def test_concurrent_same_object_writes_become_siblings() -> None:
    cluster = make_cluster()
    node = cluster.active_nodes()[0]
    shared_empty_read = []

    first = write(cluster, "k0", context=shared_empty_read, actor_id="client-a")
    second = write(cluster, "k0", context=shared_empty_read, actor_id="client-b")

    assert compare_true_histories(first, second) == "concurrent"
    assert {version.version_id for version in node.read("k0")} == {
        first.version_id,
        second.version_id,
    }


def test_merge_write_reading_siblings_dominates_both() -> None:
    cluster = make_cluster()
    node = cluster.active_nodes()[0]

    first = write(cluster, "k0", context=[], actor_id="client-a")
    second = write(cluster, "k0", context=[], actor_id="client-b")
    siblings = node.read("k0")

    merged = write(cluster, "k0", context=siblings, actor_id="client-c")

    assert compare_true_histories(merged, first) == "dominates"
    assert compare_true_histories(merged, second) == "dominates"
    assert {
        (event.key, event.actor, event.counter) for event in merged.true_history
    }.issuperset(
        {
            (first.key, first.dot.actor, first.dot.counter),
            (second.key, second.dot.actor, second.dot.counter),
            (merged.key, merged.dot.actor, merged.dot.counter),
        }
    )
    assert node.read("k0") == [merged]
