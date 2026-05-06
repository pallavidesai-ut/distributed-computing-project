from __future__ import annotations

from clocksim import (
    Dot,
    ITCCoreStamp,
    ITCEventTree,
    ITCIdTree,
    IntervalTreeClockModel,
    make_clock_factory,
)


def test_itc_core_fork_event_join_semantics() -> None:
    left, right = ITCCoreStamp.seed().fork()

    left.event_occurred()
    right.event_occurred()

    assert not left.leq(right)
    assert not right.leq(left)

    merged = left.join(right)
    descendant = merged.clone()
    descendant.event_occurred()

    assert left.leq(descendant)
    assert right.leq(descendant)
    assert not descendant.leq(left)


def test_itc_event_join_is_commutative_and_idempotent() -> None:
    first = ITCEventTree.node(0, ITCEventTree.leaf(2), ITCEventTree.leaf(0))
    second = ITCEventTree.node(0, ITCEventTree.leaf(0), ITCEventTree.leaf(3))

    assert first.join(second) == second.join(first)
    assert first.join(first) == first


def test_itc_model_preserves_read_ancestry_in_same_object_chain() -> None:
    model = IntervalTreeClockModel()
    state = model.make_state("n1")
    first_context = model.build_read_context([])
    first = model.issue_stamp(state, "k0", first_context, now=0.0, actor_id="client-a")

    second_context = model.build_read_context([])
    # Simulate the same shape the cluster passes: the read contains the first version.
    second_context.event = first.event.clone()
    second_context.context = first.represented_context()
    second = model.issue_stamp(state, "k0", second_context, now=1.0, actor_id="client-b")

    assert second.represented_context().contains(first.dot)
    assert model.compare_stamps(second, first) == "dominates"


def test_itc_model_distinguishes_concurrent_client_writes_on_same_coordinator() -> None:
    model = IntervalTreeClockModel()
    state = model.make_state("n1")
    empty = model.build_read_context([])

    first = model.issue_stamp(state, "k0", empty, now=0.0, actor_id="client-a")
    second = model.issue_stamp(state, "k0", model.build_read_context([]), now=0.0, actor_id="client-b")

    assert model.compare_stamps(first, second) == "concurrent"
    assert first.represented_context().contains(Dot("client-a", 1))
    assert second.represented_context().contains(Dot("client-b", 1))


def test_itc_is_registered_for_cli_and_experiment_comparison() -> None:
    assert make_clock_factory("itc", lease_duration=60.0) is IntervalTreeClockModel


def test_itc_identity_splits_are_disjoint_and_union_to_root() -> None:
    left, right = ITCIdTree.one().split()

    assert left.union(right) == ITCIdTree.one()
    assert left != right
