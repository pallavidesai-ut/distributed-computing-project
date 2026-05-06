from __future__ import annotations

from clocksim import (
    CausalContext,
    Dot,
    AdaptiveLeaseDottedVersionVectorModel,
    ClientDottedVersionVectorModel,
    DottedVersionVectorModel,
    LeaseClientDottedVersionVectorModel,
    LeaseDottedVersionVectorModel,
    MembershipLeaseDottedVersionVectorModel,
    VersionVectorModel,
)


def test_vv_and_dvv_preserve_read_ancestry_in_same_object_chain() -> None:
    for model in [VersionVectorModel(), DottedVersionVectorModel(), ClientDottedVersionVectorModel()]:
        state = model.make_state("n1")
        first = model.issue_stamp(state, "k0", CausalContext(), now=0.0, actor_id="client-a")
        read_context = model.build_read_context([])
        # Build the read context through the model's stamp representation without
        # constructing VersionRecord objects; this is the same context shape used
        # for a read-then-write dependency.
        read_context = first.represented_context()

        second = model.issue_stamp(state, "k0", read_context, now=1.0, actor_id="client-b")

        assert second.represented_context().contains(first.dot)
        assert model.compare_stamps(second, first) == "dominates"



def test_dvv_uses_configured_actor_identity_supplied_by_simulator() -> None:
    model = DottedVersionVectorModel()
    state = model.make_state("physical-node-1")

    first = model.issue_stamp(state, "k0", CausalContext(), now=0.0, actor_id="configured-actor")
    second = model.issue_stamp(state, "k0", CausalContext(), now=0.0, actor_id="configured-actor")

    assert first.dot.actor == "configured-actor"
    assert second.dot.actor == "configured-actor"
    assert first.dot.counter == 1
    assert second.dot.counter == 2


def test_client_dvv_uses_same_client_actor_domain_as_vv() -> None:
    model = ClientDottedVersionVectorModel()
    state = model.make_state("n1")
    first = model.issue_stamp(state, "k0", CausalContext(), now=0.0, actor_id="client-a")
    second = model.issue_stamp(state, "k0", CausalContext(), now=0.0, actor_id="client-b")
    merged = model.issue_stamp(
        state,
        "k0",
        model.build_read_context([]),
        now=1.0,
        actor_id="client-c",
    )

    assert first.dot.actor == "client-a"
    assert second.dot.actor == "client-b"
    assert model.compare_stamps(first, second) == "concurrent"
    assert merged.dot.actor == "client-c"


def test_expired_lease_client_dvv_prunes_client_actor_history() -> None:
    read_context = CausalContext(prefix={"client-b": 3})
    model = LeaseClientDottedVersionVectorModel(lease_duration=1.0)
    state = model.make_state("n1")

    stamp = model.issue_stamp(state, "k0", read_context, now=10.0, actor_id="client-a")

    assert stamp.dot.actor == "client-a"
    assert stamp.was_pruned()
    assert not stamp.represented_context().contains(Dot("client-b", 3))


def test_lease_dvv_renews_only_dot_actor_not_transitive_context() -> None:
    model = LeaseDottedVersionVectorModel(lease_duration=10.0)
    state = model.make_state("n1")
    read_context = CausalContext(prefix={"stale-actor": 5})

    stamp = model.issue_stamp(state, "k0", read_context, now=1.0, actor_id="fresh-actor")

    assert stamp.dot.actor == "fresh-actor"
    assert state.leases["k0"]["fresh-actor"] == 11.0
    assert "stale-actor" not in state.leases["k0"]


def test_long_lease_dvv_preserves_recent_observed_actor_history() -> None:
    lease_model = LeaseDottedVersionVectorModel(lease_duration=100.0)
    state = lease_model.make_state("n1")
    read_context = CausalContext(prefix={"n2": 3}, dots={Dot("n3", 1)})

    # Simulate recent observations so n2/n3 leases are still live at issue time.
    state.leases["k0"]["n2"] = 100.0
    state.leases["k0"]["n3"] = 100.0

    stamp = lease_model.issue_stamp(state, "k0", read_context, now=10.0, actor_id="client-a")

    represented = stamp.represented_context()
    assert not stamp.was_pruned()
    assert represented.contains(Dot("n2", 3))
    assert represented.contains(Dot("n3", 1))


def test_expired_lease_dvv_prunes_metadata_and_loses_recall_shape() -> None:
    read_context = CausalContext(prefix={"n2": 5, "n3": 2})

    exact_model = DottedVersionVectorModel()
    exact_state = exact_model.make_state("n1")
    exact_stamp = exact_model.issue_stamp(exact_state, "k0", read_context, now=10.0, actor_id="client-a")

    lease_model = LeaseDottedVersionVectorModel(lease_duration=1.0)
    lease_state = lease_model.make_state("n1")
    pruned_stamp = lease_model.issue_stamp(lease_state, "k0", read_context, now=10.0, actor_id="client-a")

    assert pruned_stamp.was_pruned()
    assert pruned_stamp.metadata_bytes() < exact_stamp.metadata_bytes()
    represented = pruned_stamp.represented_context().materialize()
    truth = exact_stamp.represented_context().materialize()
    assert represented < truth


def test_membership_lease_dvv_keeps_quiet_active_actors() -> None:
    read_context = CausalContext(prefix={"n2": 4})
    model = MembershipLeaseDottedVersionVectorModel(lease_duration=1.0)
    state = model.make_state("n1")
    model.update_active_actors({"n1", "n2"}, now=0.0)

    stamp = model.issue_stamp(state, "k0", read_context, now=100.0, actor_id="client-a")

    assert not stamp.was_pruned()
    assert stamp.represented_context().contains(Dot("n2", 4))


def test_membership_lease_dvv_prunes_departed_actor_after_grace_period() -> None:
    read_context = CausalContext(prefix={"n2": 4})
    model = MembershipLeaseDottedVersionVectorModel(lease_duration=5.0)
    state = model.make_state("n1")
    model.update_active_actors({"n1", "n2"}, now=0.0)
    model.update_active_actors({"n1"}, now=10.0)

    retained = model.issue_stamp(state, "k0", read_context, now=12.0, actor_id="client-a")
    pruned = model.issue_stamp(state, "k0", read_context, now=20.0, actor_id="client-a")

    assert retained.represented_context().contains(Dot("n2", 4))
    assert pruned.was_pruned()
    assert not pruned.represented_context().contains(Dot("n2", 4))


def test_shorter_lease_prunes_at_least_as_much_as_longer_lease() -> None:
    read_context = CausalContext(prefix={"n2": 4, "n3": 3}, dots={Dot("n4", 2)})

    short_model = LeaseDottedVersionVectorModel(lease_duration=5.0)
    short_state = short_model.make_state("n1")
    short_state.leases["k0"].update({"n2": 11.0, "n3": 30.0, "n4": 30.0})
    short_stamp = short_model.issue_stamp(short_state, "k0", read_context, now=20.0, actor_id="client-a")

    long_model = LeaseDottedVersionVectorModel(lease_duration=30.0)
    long_state = long_model.make_state("n1")
    long_state.leases["k0"].update({"n2": 30.0, "n3": 30.0, "n4": 30.0})
    long_stamp = long_model.issue_stamp(long_state, "k0", read_context, now=20.0, actor_id="client-a")

    assert short_stamp.pruned_event_count() >= long_stamp.pruned_event_count()
    assert short_stamp.metadata_component_count() <= long_stamp.metadata_component_count()
    assert not short_stamp.represented_context().contains(Dot("n2", 4))
    assert long_stamp.represented_context().contains(Dot("n2", 4))


def test_adaptive_lease_dvv_prunes_stale_low_risk_actor_history() -> None:
    model = AdaptiveLeaseDottedVersionVectorModel(lease_duration=10.0)
    state = model.make_state("n1")
    state.actor_last_seen["k0"]["stale"] = 0.0
    state.key_sibling_counts["k0"] = 0
    read_context = CausalContext(prefix={"stale": 4})

    stamp = model.issue_stamp(state, "k0", read_context, now=100.0, actor_id="fresh")

    assert stamp.was_pruned()
    assert not stamp.represented_context().contains(Dot("stale", 4))


def test_adaptive_lease_dvv_extends_recent_high_pressure_actor_history() -> None:
    model = AdaptiveLeaseDottedVersionVectorModel(lease_duration=10.0)
    state = model.make_state("n1")
    state.actor_last_seen["k0"]["recent"] = 8.0
    state.key_sibling_counts["k0"] = 8
    state.replication_latency_ewma = 3.0
    read_context = CausalContext(prefix={"recent": 4})

    stamp = model.issue_stamp(state, "k0", read_context, now=12.0, actor_id="fresh")

    assert not stamp.was_pruned()
    assert stamp.represented_context().contains(Dot("recent", 4))


def test_adaptive_lease_dvv_uses_per_actor_observed_cadence() -> None:
    model = AdaptiveLeaseDottedVersionVectorModel(lease_duration=100.0)
    state = model.make_state("n1")
    state.actor_last_seen["k0"].update({"fast": 100.0, "slow": 100.0})
    state.actor_gap_ewma["k0"].update({"fast": 5.0, "slow": 30.0})
    read_context = CausalContext(prefix={"fast": 3, "slow": 3})

    stamp = model.issue_stamp(state, "k0", read_context, now=125.0, actor_id="fresh")
    represented = stamp.represented_context()

    assert stamp.was_pruned()
    assert not represented.contains(Dot("fast", 3))
    assert represented.contains(Dot("slow", 3))


def test_adaptive_lease_dvv_churn_pressure_extends_above_nominal_lease() -> None:
    model = AdaptiveLeaseDottedVersionVectorModel(lease_duration=32.0)
    state = model.make_state("n1")
    state.actor_last_seen["k0"]["actor"] = 10.0
    state.actor_gap_ewma["k0"]["actor"] = 16.0
    state.membership_churn_rate_ewma = model.churn_rate_target
    state.membership_last_change_at = 12.0
    read_context = CausalContext(prefix={"actor": 3})

    lease = model.adaptive_lease_duration(
        state,
        "k0",
        "actor",
        "fresh",
        read_context,
        now=12.0,
    )

    assert lease > model.nominal_lease_duration
