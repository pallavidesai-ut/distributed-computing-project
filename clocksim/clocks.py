"""Clock stamp and clock model implementations."""

from __future__ import annotations

import json
import math
from abc import ABC, abstractmethod
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable

from .context import CausalContext, Dot, compact_context, compare_contexts, union_contexts
from .itc import ITCCoreStamp, ITCEventTree, ITCIdTree


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
class ITCStamp(BaseStamp):
    """Simulator stamp backed by a real Interval Tree Clock timestamp.

    ``context`` is a non-serialized sidecar used only by the simulator's
    ground-truth/accuracy metrics, which are expressed in object-local dots.
    Ordering decisions for ITC stamps use the ITC event tree, not this sidecar.
    """

    identity: ITCIdTree
    event: ITCEventTree
    new_dot: Dot
    context: CausalContext
    stamp_type = "itc"

    @property
    def dot(self) -> Dot:
        return self.new_dot

    def represented_context(self) -> CausalContext:
        return self.context.clone()

    def serialize(self) -> dict[str, Any]:
        return {
            "type": self.stamp_type,
            "id": self.identity.to_obj(),
            "event": self.event.to_obj(),
        }

    def metadata_component_count(self) -> int:
        return self.identity.node_count() + self.event.node_count()


@dataclass
class ITCReadContext:
    event: ITCEventTree = field(default_factory=ITCEventTree.leaf)
    context: CausalContext = field(default_factory=CausalContext)


@dataclass
class NodeClockState:
    node_id: str
    clock_actor_id: str | None = None
    local_counters: dict[str, int] = field(default_factory=dict)
    leases: dict[str, dict[str, float]] = field(default_factory=lambda: defaultdict(dict))
    actor_last_seen: dict[str, dict[str, float]] = field(default_factory=lambda: defaultdict(dict))
    actor_gap_ewma: dict[str, dict[str, float]] = field(default_factory=lambda: defaultdict(dict))
    key_sibling_counts: dict[str, int] = field(default_factory=dict)
    replication_latency_ewma: float = 0.0
    membership_churn_rate_ewma: float = 0.0
    membership_last_change_at: float | None = None
    last_adaptive_lease_min: float = 0.0
    last_adaptive_lease_avg: float = 0.0
    last_adaptive_lease_max: float = 0.0

    @property
    def actor_id(self) -> str:
        return self.clock_actor_id or self.node_id


@dataclass
class PrunedContext:
    context: CausalContext
    pruned_actors: int
    pruned_events: int


def prune_context(
    read_context: CausalContext,
    *,
    actor_is_live: Callable[[str], bool],
) -> PrunedContext:
    compacted = compact_context(read_context.prefix, set(read_context.dots))
    live_prefix: dict[str, int] = {}
    live_dots: set[Dot] = set()
    pruned_actors: set[str] = set()
    pruned_events = 0

    for actor, counter in compacted.prefix.items():
        if actor_is_live(actor):
            live_prefix[actor] = counter
        else:
            pruned_actors.add(actor)
            pruned_events += counter

    for dot in compacted.dots:
        if actor_is_live(dot.actor):
            live_dots.add(dot)
        else:
            pruned_actors.add(dot.actor)
            pruned_events += 1

    return PrunedContext(
        context=compact_context(live_prefix, live_dots),
        pruned_actors=len(pruned_actors),
        pruned_events=pruned_events,
    )


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

    def observe_delivery_latency(self, state: NodeClockState, latency: float) -> None:
        return None

    def update_system_state(
        self,
        state: NodeClockState,
        *,
        key: str,
        sibling_count: int,
        now: float,
    ) -> None:
        return None

    def observe_membership_change(self, state: NodeClockState, now: float) -> None:
        return None

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



class IntervalTreeClockModel(ClockModel):
    """Exact per-object Interval Tree Clock over dynamic client actors.

    The simulator's exact VV baseline uses client actors, so ITC does the same
    to preserve the same causality contract while replacing the fixed actor
    vector with ITC identity/event trees.  Actor identities are allocated by
    repeatedly forking a reserved seed identity; each object key has an
    independent event tree so causality remains per-object.
    """

    name = "itc"

    def __init__(self) -> None:
        self._seed_identity = ITCIdTree.one()
        self._actor_identities: dict[str, ITCIdTree] = {}
        self._actor_events: dict[str, dict[str, ITCEventTree]] = defaultdict(dict)
        self._actor_counters: dict[str, dict[str, int]] = defaultdict(dict)

    def _identity_for_actor(self, actor_id: str) -> ITCIdTree:
        identity = self._actor_identities.get(actor_id)
        if identity is None:
            actor_identity, remaining = self._seed_identity.split()
            self._actor_identities[actor_id] = actor_identity
            self._seed_identity = remaining
            identity = actor_identity
        return identity.clone()

    def build_read_context(self, versions: list["VersionRecord"]) -> ITCReadContext:
        event = ITCEventTree.leaf(0)
        contexts: list[CausalContext] = []
        for version in versions:
            if not isinstance(version.stamp, ITCStamp):
                raise TypeError("IntervalTreeClockModel can only read ITC stamps")
            event = event.join(version.stamp.event)
            contexts.append(version.stamp.represented_context())
        return ITCReadContext(event=event, context=union_contexts(contexts))

    def issue_stamp(
        self,
        state: NodeClockState,
        key: str,
        read_context: CausalContext,
        now: float,
        actor_id: str,
    ) -> BaseStamp:
        if not isinstance(read_context, ITCReadContext):
            raise TypeError("IntervalTreeClockModel requires an ITCReadContext")
        identity = self._identity_for_actor(actor_id)
        current_event = self._actor_events[actor_id].get(key, ITCEventTree.leaf(0))
        process_stamp = ITCCoreStamp(identity=identity, event=current_event.join(read_context.event))
        process_stamp.event_occurred()
        self._actor_events[actor_id][key] = process_stamp.event.clone()

        next_counter = max(
            self._actor_counters[actor_id].get(key, 0),
            read_context.context.max_counter(actor_id),
        ) + 1
        self._actor_counters[actor_id][key] = next_counter
        dot = Dot(actor_id, next_counter)
        represented = compact_context(
            read_context.context.prefix,
            set(read_context.context.dots) | {dot},
        )
        return ITCStamp(
            identity=process_stamp.identity.clone(),
            event=process_stamp.event.clone(),
            new_dot=dot,
            context=represented,
        )

    def compare_stamps(self, left: BaseStamp, right: BaseStamp) -> str:
        if not isinstance(left, ITCStamp) or not isinstance(right, ITCStamp):
            return super().compare_stamps(left, right)
        if left.event == right.event:
            return "equal"
        left_includes_right = right.event.leq(left.event)
        right_includes_left = left.event.leq(right.event)
        if left_includes_right and not right_includes_left:
            return "dominates"
        if right_includes_left and not left_includes_right:
            return "dominated"
        return "concurrent"


class DottedVersionVectorModel(ClockModel):
    """Exact per-object DVV over the configured causal actor domain."""

    name = "dvv"

    def __init__(self) -> None:
        self.actor_counters: dict[str, dict[str, int]] = defaultdict(dict)

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
            self.actor_counters[actor_id].get(key, 0),
            compacted.max_counter(actor_id),
        ) + 1
        self.actor_counters[actor_id][key] = next_counter
        dot = Dot(actor_id, next_counter)
        exceptions = set(compacted.dots)
        exceptions.discard(dot)
        return DVVStamp(
            summary=dict(compacted.prefix),
            exceptions=exceptions,
            new_dot=dot,
            type_name=self.name,
        )


class ClientDottedVersionVectorModel(DottedVersionVectorModel):
    """Exact DVV over the same client actor domain used by VV and ITC."""

    name = "dvv_client"

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
            self.actor_counters[actor_id].get(key, 0),
            compacted.max_counter(actor_id),
        ) + 1
        self.actor_counters[actor_id][key] = next_counter
        dot = Dot(actor_id, next_counter)
        exceptions = set(compacted.dots)
        exceptions.discard(dot)
        return DVVStamp(
            summary=dict(compacted.prefix),
            exceptions=exceptions,
            new_dot=dot,
            type_name=self.name,
        )


class LeaseClientDottedVersionVectorModel(ClientDottedVersionVectorModel):
    """Client-domain DVV with lease pruning for direct dynamic-actor studies."""

    name = "lease_dvv_client"

    def __init__(self, lease_duration: float) -> None:
        self.lease_duration = lease_duration
        self.client_counters: dict[str, dict[str, int]] = defaultdict(dict)

    def observe_stamp(
        self,
        state: NodeClockState,
        key: str,
        stamp: BaseStamp,
        now: float,
    ) -> None:
        # Dot-renewed leases: only direct evidence from the actor's own new
        # event renews its lease. Transitive mentions in the causal context do
        # not keep stale actors alive.
        state.leases[key][stamp.dot.actor] = now + self.lease_duration

    def issue_stamp(
        self,
        state: NodeClockState,
        key: str,
        read_context: CausalContext,
        now: float,
        actor_id: str,
    ) -> BaseStamp:
        expiries = state.leases[key]
        pruned = prune_context(
            read_context,
            actor_is_live=lambda actor: actor == actor_id or expiries.get(actor, float("-inf")) > now,
        )
        live_context = pruned.context
        next_counter = max(
            self.client_counters[actor_id].get(key, 0),
            live_context.max_counter(actor_id),
        ) + 1
        self.client_counters[actor_id][key] = next_counter
        dot = Dot(actor_id, next_counter)
        stamp = DVVStamp(
            summary=dict(live_context.prefix),
            exceptions=set(live_context.dots),
            new_dot=dot,
            type_name=self.name,
            pruned_actors=pruned.pruned_actors,
            pruned_events=pruned.pruned_events,
        )
        self.observe_stamp(state, key, stamp, now)
        return stamp


class LeaseDottedVersionVectorModel(DottedVersionVectorModel):
    name = "lease_dvv"

    def __init__(self, lease_duration: float) -> None:
        self.lease_duration = lease_duration
        self.actor_counters: dict[str, dict[str, int]] = defaultdict(dict)

    def observe_stamp(
        self,
        state: NodeClockState,
        key: str,
        stamp: BaseStamp,
        now: float,
    ) -> None:
        super().observe_stamp(state, key, stamp, now)
        # Dot-renewed leases: only direct evidence from the actor's own new
        # event renews its lease. Transitive mentions in the causal context do
        # not keep stale actors alive.
        state.leases[key][stamp.dot.actor] = now + self.lease_duration

    def issue_stamp(
        self,
        state: NodeClockState,
        key: str,
        read_context: CausalContext,
        now: float,
        actor_id: str,
    ) -> BaseStamp:
        expiries = state.leases[key]
        pruned = prune_context(
            read_context,
            actor_is_live=lambda actor: actor == actor_id or expiries.get(actor, float("-inf")) > now,
        )
        live_context = pruned.context
        next_counter = max(
            self.actor_counters[actor_id].get(key, 0),
            live_context.max_counter(actor_id),
        ) + 1
        self.actor_counters[actor_id][key] = next_counter
        dot = Dot(actor_id, next_counter)
        stamp = DVVStamp(
            summary=dict(live_context.prefix),
            exceptions=set(live_context.dots),
            new_dot=dot,
            type_name=self.name,
            pruned_actors=pruned.pruned_actors,
            pruned_events=pruned.pruned_events,
        )
        self.observe_stamp(state, key, stamp, now)
        return stamp


class AdaptiveLeaseDottedVersionVectorModel(DottedVersionVectorModel):
    """Approximate DVV with a local, per-actor suspicion lease.

    The lease is learned from local state a real replica could observe:
    per-actor/key arrival cadence, local sibling pressure for the key, observed
    replication latency, and current context width as metadata pressure.
    """

    name = "adaptive_lease_dvv"

    def __init__(self, lease_duration: float) -> None:
        self.nominal_lease_duration = max(lease_duration, 0.001)
        self.max_lease_duration = max(self.nominal_lease_duration * 2.0, 0.001)
        self.min_lease_duration = max(self.nominal_lease_duration * 0.25, 0.001)
        self.default_actor_gap = max(self.nominal_lease_duration * 0.5, self.min_lease_duration)
        self.gap_alpha = 0.3
        self.churn_alpha = 0.5
        self.churn_decay_window = max(self.nominal_lease_duration * 0.5, 0.001)
        self.latency_target = max(self.nominal_lease_duration * 0.25, 0.001)
        self.churn_rate_target = max(2.0 / self.nominal_lease_duration, 0.001)
        self.sibling_target = 8.0
        self.metadata_actor_target = 16.0
        self.actor_counters: dict[str, dict[str, int]] = defaultdict(dict)

    def observe_stamp(
        self,
        state: NodeClockState,
        key: str,
        stamp: BaseStamp,
        now: float,
    ) -> None:
        super().observe_stamp(state, key, stamp, now)
        actor = stamp.dot.actor
        previous = state.actor_last_seen[key].get(actor)
        if previous is not None and now > previous:
            gap = now - previous
            current = state.actor_gap_ewma[key].get(actor)
            state.actor_gap_ewma[key][actor] = (
                gap
                if current is None
                else self.gap_alpha * gap + (1.0 - self.gap_alpha) * current
            )
        state.actor_last_seen[key][actor] = now

    def observe_delivery_latency(self, state: NodeClockState, latency: float) -> None:
        alpha = 0.2
        if state.replication_latency_ewma <= 0.0:
            state.replication_latency_ewma = latency
        else:
            state.replication_latency_ewma = (
                alpha * latency + (1.0 - alpha) * state.replication_latency_ewma
            )

    def observe_membership_change(self, state: NodeClockState, now: float) -> None:
        previous = state.membership_last_change_at
        if previous is not None and now > previous:
            rate = 1.0 / max(now - previous, 0.001)
            if state.membership_churn_rate_ewma <= 0.0:
                state.membership_churn_rate_ewma = rate
            else:
                state.membership_churn_rate_ewma = (
                    self.churn_alpha * rate
                    + (1.0 - self.churn_alpha) * state.membership_churn_rate_ewma
                )
        state.membership_last_change_at = now

    def update_system_state(
        self,
        state: NodeClockState,
        *,
        key: str,
        sibling_count: int,
        now: float,
    ) -> None:
        state.key_sibling_counts[key] = sibling_count

    def _expected_actor_gap(self, state: NodeClockState, key: str, actor: str) -> float:
        return max(
            self.min_lease_duration,
            min(
                self.nominal_lease_duration,
                state.actor_gap_ewma[key].get(actor, self.default_actor_gap),
            ),
        )

    def _pressure_components(
        self,
        state: NodeClockState,
        key: str,
        read_context: CausalContext,
        now: float,
    ) -> tuple[float, float, float, float]:
        sibling_pressure = min(
            1.0,
            state.key_sibling_counts.get(key, 0) / self.sibling_target,
        )
        network_uncertainty = min(
            1.0,
            state.replication_latency_ewma / self.latency_target,
        )
        churn_rate = state.membership_churn_rate_ewma
        if state.membership_last_change_at is not None:
            age = max(0.0, now - state.membership_last_change_at)
            churn_rate *= math.exp(-age / self.churn_decay_window)
        churn_pressure = min(1.0, churn_rate / self.churn_rate_target)
        context_width = len(read_context.prefix) + len(read_context.dots)
        metadata_pressure = min(1.0, context_width / self.metadata_actor_target)
        return sibling_pressure, network_uncertainty, churn_pressure, metadata_pressure

    def adaptive_lease_duration(
        self,
        state: NodeClockState,
        key: str,
        actor: str,
        local_actor: str,
        read_context: CausalContext,
        now: float,
    ) -> float:
        expected_gap = self._expected_actor_gap(state, key, actor)
        (
            sibling_pressure,
            network_uncertainty,
            churn_pressure,
            metadata_pressure,
        ) = self._pressure_components(
            state,
            key,
            read_context,
            now,
        )
        delivery_slack = max(0.0, state.replication_latency_ewma)
        retention_multiplier = (
            1.0
            + 0.35 * sibling_pressure
            + 0.20 * network_uncertainty
            + 2.50 * churn_pressure
        )
        metadata_multiplier = 1.0 - 0.25 * metadata_pressure
        lease_duration = (
            (expected_gap + delivery_slack)
            * retention_multiplier
            * metadata_multiplier
        )
        return max(
            self.min_lease_duration,
            min(self.max_lease_duration, lease_duration),
        )

    def issue_stamp(
        self,
        state: NodeClockState,
        key: str,
        read_context: CausalContext,
        now: float,
        actor_id: str,
    ) -> BaseStamp:
        lease_cache: dict[str, float] = {}

        def actor_is_live(actor: str) -> bool:
            if actor == actor_id:
                return True
            last_seen = state.actor_last_seen[key].get(actor)
            if last_seen is None:
                return False
            lease_duration = self.adaptive_lease_duration(
                state,
                key,
                actor,
                actor_id,
                read_context,
                now,
            )
            lease_cache[actor] = lease_duration
            return last_seen + lease_duration > now

        pruned = prune_context(
            read_context,
            actor_is_live=actor_is_live,
        )
        if lease_cache:
            leases = list(lease_cache.values())
            state.last_adaptive_lease_min = min(leases)
            state.last_adaptive_lease_avg = sum(leases) / len(leases)
            state.last_adaptive_lease_max = max(leases)
        else:
            state.last_adaptive_lease_min = 0.0
            state.last_adaptive_lease_avg = 0.0
            state.last_adaptive_lease_max = 0.0
        live_context = pruned.context
        next_counter = max(
            self.actor_counters[actor_id].get(key, 0),
            live_context.max_counter(actor_id),
        ) + 1
        self.actor_counters[actor_id][key] = next_counter
        dot = Dot(actor_id, next_counter)
        stamp = DVVStamp(
            summary=dict(live_context.prefix),
            exceptions=set(live_context.dots),
            new_dot=dot,
            type_name=self.name,
            pruned_actors=pruned.pruned_actors,
            pruned_events=pruned.pruned_events,
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
        self.actor_counters: dict[str, dict[str, int]] = defaultdict(dict)
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
        pruned = prune_context(
            read_context,
            actor_is_live=lambda actor: self._actor_is_live(actor, actor_id, now),
        )
        live_context = pruned.context
        next_counter = max(
            self.actor_counters[actor_id].get(key, 0),
            live_context.max_counter(actor_id),
        ) + 1
        self.actor_counters[actor_id][key] = next_counter
        dot = Dot(actor_id, next_counter)
        return DVVStamp(
            summary=dict(live_context.prefix),
            exceptions=set(live_context.dots),
            new_dot=dot,
            type_name=self.name,
            pruned_actors=pruned.pruned_actors,
            pruned_events=pruned.pruned_events,
        )



def make_clock_factory(clock_name: str, lease_duration: float) -> Callable[[], ClockModel]:
    if clock_name in {"vv", "vv_client", "vector"}:
        return VersionVectorModel
    if clock_name in {"itc", "itc_client"}:
        return IntervalTreeClockModel
    if clock_name == "dvv":
        return DottedVersionVectorModel
    if clock_name == "dvv_client":
        return ClientDottedVersionVectorModel
    if clock_name == "lease_dvv":
        return lambda: LeaseDottedVersionVectorModel(lease_duration=lease_duration)
    if clock_name in {"adaptive_lease_dvv", "risk_lease_dvv"}:
        return lambda: AdaptiveLeaseDottedVersionVectorModel(lease_duration=lease_duration)
    if clock_name == "lease_dvv_client":
        return lambda: LeaseClientDottedVersionVectorModel(lease_duration=lease_duration)
    if clock_name in {"membership_lease_dvv", "churn_aware_lease_dvv"}:
        return lambda: MembershipLeaseDottedVersionVectorModel(lease_duration=lease_duration)
    raise KeyError(f"Unknown clock: {clock_name}")


CLOCK_FACTORIES: dict[str, Callable[[], ClockModel]] = {
    "dvv": DottedVersionVectorModel,
    "dvv_client": ClientDottedVersionVectorModel,
    "itc": IntervalTreeClockModel,
    "itc_client": IntervalTreeClockModel,
    "adaptive_lease_dvv": lambda: AdaptiveLeaseDottedVersionVectorModel(lease_duration=60.0),
    "lease_dvv": lambda: LeaseDottedVersionVectorModel(lease_duration=60.0),
    "lease_dvv_client": lambda: LeaseClientDottedVersionVectorModel(lease_duration=60.0),
    "membership_lease_dvv": lambda: MembershipLeaseDottedVersionVectorModel(lease_duration=60.0),
    "vector": VersionVectorModel,
    "vv": VersionVectorModel,
}
