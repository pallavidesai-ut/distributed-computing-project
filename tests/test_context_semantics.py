from __future__ import annotations

from clocksim import (
    CausalContext,
    Dot,
    compact_context,
    compare_contexts,
    context_includes,
    union_contexts,
)


def test_context_contains_prefix_events_and_explicit_dots() -> None:
    ctx = CausalContext(prefix={"a": 2}, dots={Dot("b", 4)})

    assert ctx.contains(Dot("a", 1))
    assert ctx.contains(Dot("a", 2))
    assert not ctx.contains(Dot("a", 3))
    assert ctx.contains(Dot("b", 4))
    assert not ctx.contains(Dot("b", 3))


def test_compact_context_promotes_contiguous_dots_into_prefix() -> None:
    ctx = compact_context(
        {"a": 1},
        {Dot("a", 2), Dot("a", 3), Dot("a", 5), Dot("b", 1)},
    )

    assert ctx.prefix == {"a": 3, "b": 1}
    assert ctx.dots == {Dot("a", 5)}


def test_union_contexts_merges_prefixes_and_exceptions() -> None:
    left = CausalContext(prefix={"a": 2}, dots={Dot("b", 2)})
    right = CausalContext(prefix={"a": 1, "b": 1}, dots={Dot("b", 3)})

    merged = union_contexts([left, right])

    assert merged.prefix == {"a": 2, "b": 3}
    assert merged.dots == set()


def test_compare_contexts_reports_dominance_and_concurrency() -> None:
    ancestor = CausalContext(prefix={"a": 1})
    descendant = CausalContext(prefix={"a": 2})
    independent = CausalContext(prefix={"b": 1})

    assert context_includes(descendant, ancestor)
    assert compare_contexts(descendant, ancestor) == "dominates"
    assert compare_contexts(ancestor, descendant) == "dominated"
    assert compare_contexts(ancestor, independent) == "concurrent"
