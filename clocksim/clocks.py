"""Clock stamp and clock model implementations."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable

from .context import CausalContext, Dot, compact_context, compare_contexts, union_contexts


class BaseStamp(ABC):
    stamp_type = "base"

    @property
    @abstractmethod
    def dot(self) -> Dot:
        raise NotImplementedError

    @abstractmethod
    def represented_context(self) -> CausalContext:
        raise NotImplementedError

    @abstractmethod
    def serialize(self) -> dict[str, Any]:
        raise NotImplementedError

    def actor_entries(self) -> set[str]:
        return self.represented_context().actor_entries()

    def metadata_component_count(self) -> int:
        raise NotImplementedError

    def metadata_bytes(self) -> int:
        payload = self.serialize()
        payload.pop("type", None)
        return len(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"))

    def pruned_actor_count(self) -> int:
        return 0

    def pruned_event_count(self) -> int:
        return 0

    def was_pruned(self) -> bool:
        return False


@dataclass
class VVStamp(BaseStamp):
    vector: dict[str, int]
    new_dot: Dot
    stamp_type = "vv"

    @property
    def dot(self) -> Dot:
        return self.new_dot

    def represented_context(self) -> CausalContext:
        return CausalContext(prefix=dict(self.vector), dots=set())

    def serialize(self) -> dict[str, Any]:
        return {
            "type": self.stamp_type,
            "vector": dict(sorted(self.vector.items())),
        }

    def metadata_component_count(self) -> int:
        return len(self.vector)


@dataclass
class DVVStamp(BaseStamp):
    summary: dict[str, int]
    exceptions: set[Dot]
    new_dot: Dot
    type_name: str = "dvv"
    pruned_actors: int = 0
    pruned_events: int = 0
    stamp_type = "dvv"

    @property
    def dot(self) -> Dot:
        return self.new_dot

    def represented_context(self) -> CausalContext:
        return compact_context(self.summary, set(self.exceptions) | {self.new_dot})

    def serialize(self) -> dict[str, Any]:
        return {
            "type": self.type_name,
            "summary": dict(sorted(self.summary.items())),
            "exceptions": [dot.to_list() for dot in sorted(self.exceptions)],
            "dot": self.new_dot.to_list(),
        }

    def metadata_component_count(self) -> int:
        return len(self.summary) + len(self.exceptions) + 1

    def pruned_actor_count(self) -> int:
        return self.pruned_actors

    def pruned_event_count(self) -> int:
        return self.pruned_events

    def was_pruned(self) -> bool:
        return bool(self.pruned_actors or self.pruned_events)


@dataclass
class NodeClockState:
    node_id: str
    local_counters: dict[str, int] = field(default_factory=dict)
    leases: dict[str, dict[str, float]] = field(default_factory=lambda: defaultdict(dict))


class ClockModel(ABC):
    name = "clock"

    def make_state(self, node_id: str) -> NodeClockState:
        return NodeClockState(node_id=node_id)

    @abstractmethod
    def build_read_context(self, versions: list["VersionRecord"]) -> CausalContext:
        raise NotImplementedError

    @abstractmethod
    def issue_stamp(
        self,
        state: NodeClockState,
        key: str,
        read_context: CausalContext,
        now: float,
        actor_id: str,
    ) -> BaseStamp:
        raise NotImplementedError

    def observe_stamp(
        self,
        state: NodeClockState,
        key: str,
        stamp: BaseStamp,
        now: float,
    ) -> None:
        if stamp.dot.actor == state.node_id:
            state.local_counters[key] = max(
                state.local_counters.get(key, 0),
                stamp.dot.counter,
            )

    def compare_stamps(self, left: BaseStamp, right: BaseStamp) -> str:
        return compare_contexts(left.represented_context(), right.represented_context())


class VersionVectorModel(ClockModel):
    name = "vv"

    def __init__(self) -> None:
        self.client_counters: dict[str, dict[str, int]] = defaultdict(dict)

    def build_read_context(self, versions: list["VersionRecord"]) -> CausalContext:
        vector: dict[str, int] = {}
        for version in versions:
            context = version.stamp.represented_context()
            for actor, counter in context.prefix.items():
                vector[actor] = max(vector.get(actor, 0), counter)
            for dot in context.dots:
                vector[dot.actor] = max(vector.get(dot.actor, 0), dot.counter)
        return CausalContext(prefix=vector, dots=set())

    def issue_stamp(
        self,
        state: NodeClockState,
        key: str,
        read_context: CausalContext,
        now: float,
        actor_id: str,
    ) -> BaseStamp:
        vector = dict(read_context.prefix)
        next_counter = max(
            self.client_counters[actor_id].get(key, 0),
            vector.get(actor_id, 0),
        ) + 1
        self.client_counters[actor_id][key] = next_counter
        vector[actor_id] = next_counter
        return VVStamp(vector=vector, new_dot=Dot(actor_id, next_counter))



class DottedVersionVectorModel(ClockModel):
    name = "dvv"

    def build_read_context(self, versions: list["VersionRecord"]) -> CausalContext:
        return union_contexts(version.stamp.represented_context() for version in versions)

    def issue_stamp(
        self,
        state: NodeClockState,
        key: str,
        read_context: CausalContext,
        now: float,
        actor_id: str,
    ) -> BaseStamp:
        compacted = compact_context(read_context.prefix, set(read_context.dots))
        next_counter = max(
            state.local_counters.get(key, 0),
            compacted.max_counter(state.node_id),
        ) + 1
        state.local_counters[key] = next_counter
        dot = Dot(state.node_id, next_counter)
        exceptions = set(compacted.dots)
        exceptions.discard(dot)
        return DVVStamp(
            summary=dict(compacted.prefix),
            exceptions=exceptions,
            new_dot=dot,
            type_name=self.name,
        )


class LeaseDottedVersionVectorModel(DottedVersionVectorModel):
    name = "lease_dvv"

    def __init__(self, lease_duration: float) -> None:
        self.lease_duration = lease_duration

    def observe_stamp(
        self,
        state: NodeClockState,
        key: str,
        stamp: BaseStamp,
        now: float,
    ) -> None:
        super().observe_stamp(state, key, stamp, now)
        expiry = now + self.lease_duration
        for actor in stamp.actor_entries():
            state.leases[key][actor] = expiry

    def issue_stamp(
        self,
        state: NodeClockState,
        key: str,
        read_context: CausalContext,
        now: float,
        actor_id: str,
    ) -> BaseStamp:
        compacted = compact_context(read_context.prefix, set(read_context.dots))
        live_prefix: dict[str, int] = {}
        live_dots: set[Dot] = set()
        pruned_actors: set[str] = set()
        pruned_events = 0
        expiries = state.leases[key]

        for actor, counter in compacted.prefix.items():
            if actor == state.node_id or expiries.get(actor, float("-inf")) > now:
                live_prefix[actor] = counter
            else:
                pruned_actors.add(actor)
                pruned_events += counter

        for dot in compacted.dots:
            if dot.actor == state.node_id or expiries.get(dot.actor, float("-inf")) > now:
                live_dots.add(dot)
            else:
                pruned_actors.add(dot.actor)
                pruned_events += 1

        live_context = compact_context(live_prefix, live_dots)
        next_counter = max(
            state.local_counters.get(key, 0),
            live_context.max_counter(state.node_id),
        ) + 1
        state.local_counters[key] = next_counter
        dot = Dot(state.node_id, next_counter)
        stamp = DVVStamp(
            summary=dict(live_context.prefix),
            exceptions=set(live_context.dots),
            new_dot=dot,
            type_name=self.name,
            pruned_actors=len(pruned_actors),
            pruned_events=pruned_events,
        )
        self.observe_stamp(state, key, stamp, now)
        return stamp


class MembershipLeaseDottedVersionVectorModel(DottedVersionVectorModel):
    """DVV that prunes only actors that have left membership and aged out.

    This keeps active replica actors regardless of how recently they were
    observed, so stable clusters behave like exact DVV. Once an actor leaves,
    its context is retained for ``lease_duration`` before it can be pruned.
    """

    name = "membership_lease_dvv"

    def __init__(self, lease_duration: float) -> None:
        self.lease_duration = lease_duration
        self.active_actors: set[str] = set()
        self.departure_expiry: dict[str, float] = {}

    def update_active_actors(self, active_actors: set[str], now: float) -> None:
        previously_active = set(self.active_actors)
        self.active_actors = set(active_actors)
        for actor in previously_active - self.active_actors:
            self.departure_expiry[actor] = now + self.lease_duration
        for actor in self.active_actors:
            self.departure_expiry.pop(actor, None)

    def _actor_is_live(self, actor: str, local_actor: str, now: float) -> bool:
        if actor == local_actor:
            return True
        if actor in self.active_actors:
            return True
        return self.departure_expiry.get(actor, float("-inf")) > now

    def issue_stamp(
        self,
        state: NodeClockState,
        key: str,
        read_context: CausalContext,
        now: float,
        actor_id: str,
    ) -> BaseStamp:
        compacted = compact_context(read_context.prefix, set(read_context.dots))
        live_prefix: dict[str, int] = {}
        live_dots: set[Dot] = set()
        pruned_actors: set[str] = set()
        pruned_events = 0

        for actor, counter in compacted.prefix.items():
            if self._actor_is_live(actor, state.node_id, now):
                live_prefix[actor] = counter
            else:
                pruned_actors.add(actor)
                pruned_events += counter

        for dot in compacted.dots:
            if self._actor_is_live(dot.actor, state.node_id, now):
                live_dots.add(dot)
            else:
                pruned_actors.add(dot.actor)
                pruned_events += 1

        live_context = compact_context(live_prefix, live_dots)
        next_counter = max(
            state.local_counters.get(key, 0),
            live_context.max_counter(state.node_id),
        ) + 1
        state.local_counters[key] = next_counter
        dot = Dot(state.node_id, next_counter)
        return DVVStamp(
            summary=dict(live_context.prefix),
            exceptions=set(live_context.dots),
            new_dot=dot,
            type_name=self.name,
            pruned_actors=len(pruned_actors),
            pruned_events=pruned_events,
        )



def make_clock_factory(clock_name: str, lease_duration: float) -> Callable[[], ClockModel]:
    if clock_name in {"vv", "vector"}:
        return VersionVectorModel
    if clock_name == "dvv":
        return DottedVersionVectorModel
    if clock_name == "lease_dvv":
        return lambda: LeaseDottedVersionVectorModel(lease_duration=lease_duration)
    if clock_name in {"membership_lease_dvv", "churn_aware_lease_dvv"}:
        return lambda: MembershipLeaseDottedVersionVectorModel(lease_duration=lease_duration)
    raise KeyError(f"Unknown clock: {clock_name}")


CLOCK_FACTORIES: dict[str, Callable[[], ClockModel]] = {
    "dvv": DottedVersionVectorModel,
    "lease_dvv": lambda: LeaseDottedVersionVectorModel(lease_duration=60.0),
    "membership_lease_dvv": lambda: MembershipLeaseDottedVersionVectorModel(lease_duration=60.0),
    "vector": VersionVectorModel,
    "vv": VersionVectorModel,
}

