from __future__ import annotations

from clocksim import (
    CausalContext,
    Dot,
    DottedVersionVectorModel,
    LeaseDottedVersionVectorModel,
    VersionVectorModel,
    VnodeVersionVectorModel,
)


def test_vv_and_dvv_preserve_read_ancestry_in_same_object_chain() -> None:
    for model in [VersionVectorModel(), DottedVersionVectorModel()]:
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


def test_vnode_vv_collapses_independent_same_coordinator_writes() -> None:
    model = VnodeVersionVectorModel()
    state = model.make_state("n1")

    first = model.issue_stamp(state, "k0", CausalContext(), now=0.0, actor_id="client-a")
    second = model.issue_stamp(state, "k0", CausalContext(), now=0.0, actor_id="client-b")

    assert model.compare_stamps(second, first) == "dominates"


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
