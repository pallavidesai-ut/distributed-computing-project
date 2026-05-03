"""Causal context data structures and comparison helpers."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable


@dataclass(frozen=True, order=True)
class Dot:
    actor: str
    counter: int

    def to_list(self) -> list[Any]:
        return [self.actor, self.counter]


@dataclass(frozen=True, order=True)
class EventId:
    """Object-scoped identity for a simulated write event.

    Clock metadata intentionally remains a plain actor/counter dot. Ground-truth
    history needs the object key too because this simulator uses per-object
    counters for replica-actor clocks, so n1:1 on k0 and n1:1 on k1 are distinct
    events.
    """

    key: str
    actor: str
    counter: int

    @classmethod
    def from_dot(cls, key: str, dot: Dot) -> "EventId":
        return cls(key=key, actor=dot.actor, counter=dot.counter)


def max_counter_for_actor(dots: Iterable[Dot], actor: str) -> int:
    return max((dot.counter for dot in dots if dot.actor == actor), default=0)


@dataclass
class CausalContext:
    prefix: dict[str, int] = field(default_factory=dict)
    dots: set[Dot] = field(default_factory=set)

    def clone(self) -> "CausalContext":
        return CausalContext(prefix=dict(self.prefix), dots=set(self.dots))

    def actor_entries(self) -> set[str]:
        actors = set(self.prefix.keys())
        actors.update(dot.actor for dot in self.dots)
        return actors

    def event_count(self) -> int:
        return sum(self.prefix.values()) + len(self.dots)

    def max_counter(self, actor: str) -> int:
        return max(self.prefix.get(actor, 0), max_counter_for_actor(self.dots, actor))

    def contains(self, dot: Dot) -> bool:
        return dot.counter <= self.prefix.get(dot.actor, 0) or dot in self.dots

    def materialize(self) -> set[Dot]:
        events: set[Dot] = set()
        for actor, counter in self.prefix.items():
            for value in range(1, counter + 1):
                events.add(Dot(actor, value))
        events.update(self.dots)
        return events


def compact_context(prefix: dict[str, int], dots: set[Dot]) -> CausalContext:
    merged_prefix = {actor: value for actor, value in prefix.items() if value > 0}
    by_actor: dict[str, set[int]] = defaultdict(set)
    for dot in dots:
        if dot.counter > merged_prefix.get(dot.actor, 0):
            by_actor[dot.actor].add(dot.counter)

    compacted_dots: set[Dot] = set()
    for actor, counters in by_actor.items():
        cursor = merged_prefix.get(actor, 0)
        while cursor + 1 in counters:
            cursor += 1
            counters.remove(cursor)
        merged_prefix[actor] = cursor
        for counter in counters:
            compacted_dots.add(Dot(actor, counter))

    return CausalContext(prefix=merged_prefix, dots=compacted_dots)


def union_contexts(contexts: Iterable[CausalContext]) -> CausalContext:
    prefix: dict[str, int] = {}
    dots: set[Dot] = set()
    for context in contexts:
        for actor, counter in context.prefix.items():
            prefix[actor] = max(prefix.get(actor, 0), counter)
        dots.update(context.dots)
    return compact_context(prefix, dots)


def context_includes(left: CausalContext, right: CausalContext) -> bool:
    for actor, counter in right.prefix.items():
        if left.prefix.get(actor, 0) < counter:
            return False
    return all(left.contains(dot) for dot in right.dots)


def compare_contexts(left: CausalContext, right: CausalContext) -> str:
    if left.prefix == right.prefix and left.dots == right.dots:
        return "equal"
    left_includes_right = context_includes(left, right)
    right_includes_left = context_includes(right, left)
    if left_includes_right and not right_includes_left:
        return "dominates"
    if right_includes_left and not left_includes_right:
        return "dominated"
    return "concurrent"
