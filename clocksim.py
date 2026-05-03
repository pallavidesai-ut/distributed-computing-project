"""
Per-object causality simulator for comparing VV, DVV, and lease-pruned DVV.

The simulator keeps two views of history at the same time:

1. Ground truth causal history, derived from read-then-write dependencies.
2. Clock-encoded history, derived from the selected metadata scheme.

This separation lets the analysis measure both metadata cost and semantic error:
exact VV and exact DVV preserve ancestry, while lease-DVV can lose ancestry after
pruning under churn.
"""

from __future__ import annotations

import copy
import csv
import heapq
import json
import math
import random
from abc import ABC, abstractmethod
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

import configargparse


def round_float(value: float, digits: int = 4) -> float:
    return round(value, digits)


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * pct
    low = math.floor(rank)
    high = math.ceil(rank)
    if low == high:
        return ordered[low]
    weight = rank - low
    return ordered[low] * (1 - weight) + ordered[high] * weight


class Environment:
    """Minimal discrete-event simulation core."""

    def __init__(self) -> None:
        self._queue: list[tuple[float, int, Callable[[], None]]] = []
        self._seq = 0
        self.now = 0.0

    def schedule(self, delay: float, callback: Callable[[], None]) -> None:
        heapq.heappush(self._queue, (self.now + delay, self._seq, callback))
        self._seq += 1

    def run(self, until: float) -> None:
        while self._queue:
            t, _, callback = self._queue[0]
            if t > until:
                break
            heapq.heappop(self._queue)
            self.now = t
            callback()


@dataclass(frozen=True, order=True)
class Dot:
    actor: str
    counter: int

    def to_list(self) -> list[Any]:
        return [self.actor, self.counter]


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


class VnodeVersionVectorModel(ClockModel):
    name = "vv_vnode"

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
            state.local_counters.get(key, 0),
            vector.get(state.node_id, 0),
        ) + 1
        state.local_counters[key] = next_counter
        vector[state.node_id] = next_counter
        return VVStamp(vector=vector, new_dot=Dot(state.node_id, next_counter))


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


def compare_true_histories(left: "VersionRecord", right: "VersionRecord") -> str:
    if left.true_history == right.true_history:
        return "equal"
    if right.true_history.issubset(left.true_history):
        return "dominates"
    if left.true_history.issubset(right.true_history):
        return "dominated"
    return "concurrent"


@dataclass
class Message:
    sender_id: str
    receiver_id: str
    key: str
    version: "VersionRecord"
    sent_at: float


@dataclass
class VersionRecord:
    version_id: str
    key: str
    stamp: BaseStamp
    origin: str
    created_at: float
    phase: str
    read_size: int
    true_history: set[Dot]

    @property
    def dot(self) -> Dot:
        return self.stamp.dot


class MetricsCollector:
    def __init__(self) -> None:
        self.writes: list[dict[str, Any]] = []
        self.deliveries: list[dict[str, Any]] = []
        self.decisions: list[dict[str, Any]] = []
        self.snapshots: list[dict[str, Any]] = []
        self.joins: list[dict[str, Any]] = []
        self.leaves: list[dict[str, Any]] = []
        self.accuracy: list[dict[str, Any]] = []

    def record_write(
        self,
        *,
        t: float,
        version: VersionRecord,
        node_id: str,
        actor_id: str,
        metadata_bytes: int,
        metadata_components: int,
        actor_entries: int,
        represented_events: int,
        true_events: int,
        is_hot_key: bool,
        target_replicas: int,
    ) -> None:
        self.writes.append(
            {
                "t": round_float(t),
                "version_id": version.version_id,
                "node": node_id,
                "actor_id": actor_id,
                "key": version.key,
                "phase": version.phase,
                "read_size": version.read_size,
                "metadata_type": version.stamp.serialize()["type"],
                "metadata_bytes": metadata_bytes,
                "metadata_components": metadata_components,
                "actor_entries": actor_entries,
                "represented_events": represented_events,
                "true_events": true_events,
                "pruned_actors": version.stamp.pruned_actor_count(),
                "pruned_events": version.stamp.pruned_event_count(),
                "was_pruned": int(version.stamp.was_pruned()),
                "target_replicas": target_replicas,
                "is_hot_key": int(is_hot_key),
            }
        )

    def record_accuracy(
        self,
        *,
        t: float,
        version: VersionRecord,
        false_positive_events: int,
        false_negative_events: int,
        precision: float,
        recall: float,
        is_hot_key: bool,
    ) -> None:
        self.accuracy.append(
            {
                "t": round_float(t),
                "version_id": version.version_id,
                "key": version.key,
                "node": version.origin,
                "precision": round_float(precision),
                "recall": round_float(recall),
                "false_positive_events": false_positive_events,
                "false_negative_events": false_negative_events,
                "was_pruned": int(version.stamp.was_pruned()),
                "is_hot_key": int(is_hot_key),
            }
        )

    def record_delivery(
        self,
        *,
        t: float,
        version_id: str,
        key: str,
        sender: str,
        receiver: str,
        latency: float,
        action: str,
        sibling_count_after: int,
    ) -> None:
        self.deliveries.append(
            {
                "t": round_float(t),
                "version_id": version_id,
                "key": key,
                "sender": sender,
                "receiver": receiver,
                "latency": round_float(latency),
                "action": action,
                "sibling_count_after": sibling_count_after,
            }
        )

    def record_decision(
        self,
        *,
        t: float,
        node_id: str,
        key: str,
        incoming_id: str,
        existing_id: str,
        true_relation: str,
        clock_relation: str,
        final_action: str,
        is_hot_key: bool,
    ) -> None:
        self.decisions.append(
            {
                "t": round_float(t),
                "node": node_id,
                "key": key,
                "incoming_id": incoming_id,
                "existing_id": existing_id,
                "true_relation": true_relation,
                "clock_relation": clock_relation,
                "final_action": final_action,
                "is_hot_key": int(is_hot_key),
            }
        )

    def record_snapshot(
        self,
        *,
        t: float,
        active_nodes: int,
        avg_versions_per_key: float,
        avg_hot_key_siblings: float,
        max_hot_key_siblings: int,
        avg_metadata_bytes: float,
        avg_actor_entries: float,
        avg_stale_actor_fraction: float,
    ) -> None:
        self.snapshots.append(
            {
                "t": round_float(t),
                "active_nodes": active_nodes,
                "avg_versions_per_key": round_float(avg_versions_per_key),
                "avg_hot_key_siblings": round_float(avg_hot_key_siblings),
                "max_hot_key_siblings": max_hot_key_siblings,
                "avg_metadata_bytes": round_float(avg_metadata_bytes),
                "avg_actor_entries": round_float(avg_actor_entries),
                "avg_stale_actor_fraction": round_float(avg_stale_actor_fraction),
            }
        )

    def record_join(self, node_id: str, cluster_size: int, t: float) -> None:
        self.joins.append(
            {
                "t": round_float(t),
                "node": node_id,
                "cluster_size": cluster_size,
            }
        )

    def record_leave(self, node_id: str, cluster_size: int, t: float) -> None:
        self.leaves.append(
            {
                "t": round_float(t),
                "node": node_id,
                "cluster_size": cluster_size,
            }
        )

    def save(self, output_dir: Path, run_name: str) -> None:
        output_dir.mkdir(parents=True, exist_ok=True)
        for name, rows in [
            ("writes", self.writes),
            ("deliveries", self.deliveries),
            ("decisions", self.decisions),
            ("snapshots", self.snapshots),
            ("joins", self.joins),
            ("leaves", self.leaves),
            ("accuracy", self.accuracy),
        ]:
            if not rows:
                continue
            path = output_dir / f"{run_name}_{name}.csv"
            with path.open("w", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
                writer.writeheader()
                writer.writerows(rows)

    def summary(self, sim_time: float) -> dict[str, Any]:
        metadata_bytes = [float(row["metadata_bytes"]) for row in self.writes]
        actor_entries = [float(row["actor_entries"]) for row in self.writes]
        precision = [float(row["precision"]) for row in self.accuracy]
        recall = [float(row["recall"]) for row in self.accuracy]
        stale_actor_fraction = [
            float(row["avg_stale_actor_fraction"]) for row in self.snapshots
        ]
        hot_siblings = [float(row["avg_hot_key_siblings"]) for row in self.snapshots]
        versions_per_key = [float(row["avg_versions_per_key"]) for row in self.snapshots]
        latency = [float(row["latency"]) for row in self.deliveries]
        pruned_writes = sum(int(row["was_pruned"]) for row in self.writes)

        conflict_pairs = [
            row for row in self.decisions if row["true_relation"] == "concurrent"
        ]
        missed_conflicts = [
            row
            for row in conflict_pairs
            if row["final_action"] in {"drop_existing", "drop_incoming"}
        ]
        descendant_pairs = [
            row for row in self.decisions if row["true_relation"] in {"dominates", "dominated"}
        ]
        stale_sibling_pairs = [
            row
            for row in descendant_pairs
            if row["final_action"] == "keep_both"
        ]

        return {
            "total_writes": len(self.writes),
            "total_replication_deliveries": len(self.deliveries),
            "joins": len(self.joins),
            "leaves": len(self.leaves),
            "avg_metadata_bytes": round_float(sum(metadata_bytes) / len(metadata_bytes), 3)
            if metadata_bytes
            else 0.0,
            "p95_metadata_bytes": round_float(percentile(metadata_bytes, 0.95), 3),
            "avg_actor_entries": round_float(sum(actor_entries) / len(actor_entries), 3)
            if actor_entries
            else 0.0,
            "avg_history_precision": round_float(sum(precision) / len(precision), 4)
            if precision
            else 0.0,
            "avg_history_recall": round_float(sum(recall) / len(recall), 4)
            if recall
            else 0.0,
            "avg_hot_key_siblings": round_float(sum(hot_siblings) / len(hot_siblings), 3)
            if hot_siblings
            else 0.0,
            "p95_hot_key_siblings": round_float(percentile(hot_siblings, 0.95), 3),
            "avg_versions_per_key": round_float(sum(versions_per_key) / len(versions_per_key), 3)
            if versions_per_key
            else 0.0,
            "avg_stale_actor_fraction": round_float(
                sum(stale_actor_fraction) / len(stale_actor_fraction),
                4,
            )
            if stale_actor_fraction
            else 0.0,
            "avg_latency": round_float(sum(latency) / len(latency), 3) if latency else 0.0,
            "latency_p95": round_float(percentile(latency, 0.95), 3),
            "pruned_write_rate": round_float(pruned_writes / len(self.writes), 4)
            if self.writes
            else 0.0,
            "missed_conflict_rate": round_float(
                len(missed_conflicts) / len(conflict_pairs), 4
            )
            if conflict_pairs
            else 0.0,
            "stale_sibling_rate": round_float(
                len(stale_sibling_pairs) / len(descendant_pairs), 4
            )
            if descendant_pairs
            else 0.0,
            "logical_write_throughput": round_float(len(self.writes) / sim_time, 3)
            if sim_time
            else 0.0,
        }


CHURN_PROFILES = {
    "stable": {"join_rate": 0.0, "leave_rate": 0.0, "burst_size": 0, "burst_interval": None},
    "low": {"join_rate": 0.01, "leave_rate": 0.01, "burst_size": 0, "burst_interval": None},
    "sustained": {"join_rate": 0.035, "leave_rate": 0.035, "burst_size": 0, "burst_interval": None},
    "burst": {"join_rate": 0.01, "leave_rate": 0.01, "burst_size": 6, "burst_interval": 45.0},
}


@dataclass
class WorkloadConfig:
    key_count: int
    hot_key_probability: float
    client_count: int
    write_interval: float
    client_think_time: float
    merge_probability: float
    burst_interval: float
    burst_writers: int
    burst_spread: float
    merge_delay: float
    same_coordinator_probability: float
    replication_factor: int


class Node:
    def __init__(
        self,
        *,
        env: Environment,
        node_id: str,
        cluster: "Cluster",
        clock_model: ClockModel,
        metrics: MetricsCollector,
    ) -> None:
        self.env = env
        self.id = node_id
        self.cluster = cluster
        self.clock_model = clock_model
        self.metrics = metrics
        self.state = clock_model.make_state(node_id)
        self.kv: dict[str, list[VersionRecord]] = {}
        self.active = True

    def read(self, key: str) -> list[VersionRecord]:
        return list(self.kv.get(key, []))

    def bootstrap_from(self, donor: "Node") -> None:
        self.kv = copy.deepcopy(donor.kv)
        for key, versions in self.kv.items():
            for version in versions:
                self.clock_model.observe_stamp(self.state, key, version.stamp, self.env.now)

    def apply_version(self, version: VersionRecord) -> tuple[str, int]:
        self.clock_model.observe_stamp(self.state, version.key, version.stamp, self.env.now)
        versions = self.kv.setdefault(version.key, [])
        comparisons: list[tuple[VersionRecord, str, str]] = []
        for existing in versions:
            comparisons.append(
                (
                    existing,
                    compare_true_histories(version, existing),
                    self.clock_model.compare_stamps(version.stamp, existing.stamp),
                )
            )

        incoming_dropped = any(clock_relation in {"dominated", "equal"} for _, _, clock_relation in comparisons)
        if incoming_dropped:
            final_action = "drop_incoming"
            for existing, true_relation, clock_relation in comparisons:
                self.metrics.record_decision(
                    t=self.env.now,
                    node_id=self.id,
                    key=version.key,
                    incoming_id=version.version_id,
                    existing_id=existing.version_id,
                    true_relation=true_relation,
                    clock_relation=clock_relation,
                    final_action=final_action,
                    is_hot_key=(version.key == "k0"),
                )
            return final_action, len(versions)

        kept: list[VersionRecord] = []
        dropped_existing = 0
        for existing, true_relation, clock_relation in comparisons:
            if clock_relation == "dominates":
                dropped_existing += 1
                action = "drop_existing"
            else:
                kept.append(existing)
                action = "keep_both"
            self.metrics.record_decision(
                t=self.env.now,
                node_id=self.id,
                key=version.key,
                incoming_id=version.version_id,
                existing_id=existing.version_id,
                true_relation=true_relation,
                clock_relation=clock_relation,
                final_action=action,
                is_hot_key=(version.key == "k0"),
            )

        kept.append(version)
        self.kv[version.key] = kept
        if dropped_existing:
            return "drop_existing", len(kept)
        if comparisons:
            return "keep_both", len(kept)
        return "insert", len(kept)


class Cluster:
    def __init__(
        self,
        *,
        env: Environment,
        metrics: MetricsCollector,
        clock_model: ClockModel,
        profile: str,
        initial_size: int,
        max_nodes: int,
        min_nodes: int,
        min_lat: float,
        max_lat: float,
        sample_interval: float,
        workload: WorkloadConfig,
    ) -> None:
        self.env = env
        self.metrics = metrics
        self.clock_model = clock_model
        self.profile = CHURN_PROFILES[profile]
        self.max_nodes = max_nodes
        self.min_nodes = min_nodes
        self.min_lat = min_lat
        self.max_lat = max_lat
        self.sample_interval = sample_interval
        self.workload = workload
        self.nodes: list[Node] = []
        self.node_counter = 0
        self.version_counter = 0
        self.session_counter = 0
        self.clients = [f"c{index:04d}" for index in range(1, workload.client_count + 1)]

        for _ in range(initial_size):
            self._add_node()

    def active_nodes(self) -> list[Node]:
        return [node for node in self.nodes if node.active]

    def active_node_ids(self) -> set[str]:
        return {node.id for node in self.active_nodes()}

    def active_count(self) -> int:
        return len(self.active_nodes())

    def choose_node(self) -> Node | None:
        active = self.active_nodes()
        return random.choice(active) if active else None

    def choose_key(self) -> str:
        if self.workload.key_count <= 1 or random.random() < self.workload.hot_key_probability:
            return "k0"
        return f"k{random.randint(1, self.workload.key_count - 1)}"

    def choose_client(self) -> str:
        return random.choice(self.clients)

    def allocate_session_actor(self) -> str:
        self.session_counter += 1
        return f"{self.choose_client()}.s{self.session_counter:06d}"

    def replication_targets(self, coordinator: Node) -> list[Node]:
        peers = [node for node in self.active_nodes() if node.id != coordinator.id]
        max_targets = max(self.workload.replication_factor - 1, 0)
        if max_targets <= 0 or not peers:
            return []
        if max_targets >= len(peers):
            return peers
        return random.sample(peers, max_targets)

    def _next_version_id(self, key: str, dot: Dot) -> str:
        self.version_counter += 1
        return f"{key}:{dot.actor}:{dot.counter}:v{self.version_counter}"

    def _record_accuracy(self, version: VersionRecord) -> None:
        represented = version.stamp.represented_context().materialize()
        truth = set(version.true_history)
        true_positive = len(represented & truth)
        false_positive = len(represented - truth)
        false_negative = len(truth - represented)
        precision = true_positive / len(represented) if represented else 1.0
        recall = true_positive / len(truth) if truth else 1.0
        self.metrics.record_accuracy(
            t=self.env.now,
            version=version,
            false_positive_events=false_positive,
            false_negative_events=false_negative,
            precision=precision,
            recall=recall,
            is_hot_key=(version.key == "k0"),
        )

    def execute_write(
        self,
        *,
        key: str,
        coordinator: Node,
        context_versions: list[VersionRecord],
        phase: str,
        actor_id: str,
    ) -> None:
        read_context = self.clock_model.build_read_context(context_versions)
        stamp = self.clock_model.issue_stamp(
            coordinator.state,
            key,
            read_context,
            self.env.now,
            actor_id,
        )
        true_history: set[Dot] = {stamp.dot}
        for version in context_versions:
            true_history.update(version.true_history)
        version = VersionRecord(
            version_id=self._next_version_id(key, stamp.dot),
            key=key,
            stamp=stamp,
            origin=coordinator.id,
            created_at=self.env.now,
            phase=phase,
            read_size=len(context_versions),
            true_history=true_history,
        )

        coordinator.apply_version(version)
        self._record_accuracy(version)
        represented_context = version.stamp.represented_context()
        targets = self.replication_targets(coordinator)
        self.metrics.record_write(
            t=self.env.now,
            version=version,
            node_id=coordinator.id,
            actor_id=actor_id,
            metadata_bytes=version.stamp.metadata_bytes(),
            metadata_components=version.stamp.metadata_component_count(),
            actor_entries=len(version.stamp.actor_entries()),
            represented_events=represented_context.event_count(),
            true_events=len(version.true_history),
            is_hot_key=(key == "k0"),
            target_replicas=len(targets) + 1,
        )

        for target in targets:
            message = Message(
                sender_id=coordinator.id,
                receiver_id=target.id,
                key=key,
                version=version,
                sent_at=self.env.now,
            )
            delay = random.uniform(self.min_lat, self.max_lat)
            self.env.schedule(delay, self._make_delivery(message))

    def _make_delivery(self, message: Message) -> Callable[[], None]:
        def deliver() -> None:
            receiver = next((node for node in self.nodes if node.id == message.receiver_id), None)
            if receiver is None or not receiver.active:
                return
            action, sibling_count_after = receiver.apply_version(message.version)
            self.metrics.record_delivery(
                t=self.env.now,
                version_id=message.version.version_id,
                key=message.key,
                sender=message.sender_id,
                receiver=message.receiver_id,
                latency=self.env.now - message.sent_at,
                action=action,
                sibling_count_after=sibling_count_after,
            )

        return deliver

    def _schedule_background_client(self) -> None:
        delay = random.expovariate(1.0 / self.workload.write_interval)

        def op() -> None:
            coordinator = self.choose_node()
            if coordinator is not None:
                key = self.choose_key()
                client_id = self.allocate_session_actor()
                read_versions = coordinator.read(key)
                phase = "merge" if len(read_versions) > 1 and random.random() < self.workload.merge_probability else "background"
                target_node = coordinator

                def commit() -> None:
                    if not target_node.active:
                        fallback = self.choose_node()
                        if fallback is None:
                            return
                        self.execute_write(
                            key=key,
                            coordinator=fallback,
                            context_versions=read_versions,
                            phase=phase,
                            actor_id=client_id,
                        )
                        return
                    self.execute_write(
                        key=key,
                        coordinator=target_node,
                        context_versions=read_versions,
                        phase=phase,
                        actor_id=client_id,
                    )

                think = random.expovariate(1.0 / self.workload.client_think_time)
                self.env.schedule(think, commit)
            self._schedule_background_client()

        self.env.schedule(delay, op)

    def _schedule_contention_burst(self) -> None:
        interval = self.workload.burst_interval

        def burst() -> None:
            anchor = self.choose_node()
            if anchor is not None:
                key = "k0"
                shared_context = anchor.read(key)
                writers = max(2, self.workload.burst_writers)
                for _ in range(writers):
                    coordinator = anchor
                    client_id = self.allocate_session_actor()
                    if random.random() > self.workload.same_coordinator_probability:
                        alternative = self.choose_node()
                        if alternative is not None:
                            coordinator = alternative

                    def burst_write(target: Node = coordinator, writer_id: str = client_id) -> None:
                        if not target.active:
                            target = self.choose_node()
                            if target is None:
                                return
                        self.execute_write(
                            key=key,
                            coordinator=target,
                            context_versions=shared_context,
                            phase="burst",
                            actor_id=writer_id,
                        )

                    self.env.schedule(random.uniform(0.0, self.workload.burst_spread), burst_write)

                def merge_write() -> None:
                    target = self.choose_node()
                    if target is None:
                        return
                    self.execute_write(
                        key=key,
                        coordinator=target,
                        context_versions=target.read(key),
                        phase="burst_merge",
                        actor_id=self.allocate_session_actor(),
                    )

                self.env.schedule(self.workload.merge_delay, merge_write)

            self.env.schedule(interval, burst)

        self.env.schedule(interval, burst)

    def _add_node(self) -> None:
        self.node_counter += 1
        node = Node(
            env=self.env,
            node_id=f"n{self.node_counter:04d}",
            cluster=self,
            clock_model=self.clock_model,
            metrics=self.metrics,
        )
        donors = self.active_nodes()
        if donors:
            node.bootstrap_from(random.choice(donors))
        self.nodes.append(node)
        self.metrics.record_join(node.id, self.active_count(), self.env.now)

    def _remove_node(self) -> None:
        active = self.active_nodes()
        if len(active) <= self.min_nodes:
            return
        victim = random.choice(active)
        victim.active = False
        self.metrics.record_leave(victim.id, self.active_count(), self.env.now)

    def _schedule_churn_event(self) -> None:
        join_rate = self.profile["join_rate"]
        leave_rate = self.profile["leave_rate"]
        total_rate = join_rate + leave_rate
        if total_rate <= 0:
            return
        delay = random.expovariate(total_rate)

        def churn() -> None:
            if random.random() < join_rate / total_rate:
                if self.active_count() < self.max_nodes:
                    self._add_node()
            else:
                self._remove_node()
            self._schedule_churn_event()

        self.env.schedule(delay, churn)

    def _schedule_burst_churn(self) -> None:
        burst_size = self.profile["burst_size"]
        interval = self.profile["burst_interval"]
        if burst_size <= 0 or interval is None:
            return

        def burst() -> None:
            for _ in range(burst_size):
                self._remove_node()

            def rejoin() -> None:
                for _ in range(burst_size):
                    if self.active_count() < self.max_nodes:
                        self._add_node()
                self.env.schedule(interval, burst)

            self.env.schedule(interval / 2, rejoin)

        self.env.schedule(interval, burst)

    def _schedule_snapshot(self) -> None:
        def snapshot() -> None:
            active = self.active_nodes()
            active_ids = self.active_node_ids()
            if not active:
                self.metrics.record_snapshot(
                    t=self.env.now,
                    active_nodes=0,
                    avg_versions_per_key=0.0,
                    avg_hot_key_siblings=0.0,
                    max_hot_key_siblings=0,
                    avg_metadata_bytes=0.0,
                    avg_actor_entries=0.0,
                    avg_stale_actor_fraction=0.0,
                )
            else:
                version_counts: list[int] = []
                hot_sibling_counts: list[int] = []
                metadata_bytes: list[int] = []
                actor_entries: list[int] = []
                stale_actor_fractions: list[float] = []
                for node in active:
                    key_versions = sum(len(versions) for versions in node.kv.values())
                    version_counts.append(key_versions / max(len(node.kv), 1) if node.kv else 0.0)
                    hot_sibling_counts.append(len(node.kv.get("k0", [])))
                    for versions in node.kv.values():
                        for version in versions:
                            metadata_bytes.append(version.stamp.metadata_bytes())
                            actor_set = version.stamp.actor_entries()
                            actor_entries.append(len(actor_set))
                            replica_actor_set = {actor for actor in actor_set if actor.startswith("n")}
                            stale_count = len(replica_actor_set - active_ids)
                            stale_actor_fractions.append(
                                stale_count / len(replica_actor_set) if replica_actor_set else 0.0
                            )
                self.metrics.record_snapshot(
                    t=self.env.now,
                    active_nodes=len(active),
                    avg_versions_per_key=sum(version_counts) / len(version_counts),
                    avg_hot_key_siblings=sum(hot_sibling_counts) / len(hot_sibling_counts),
                    max_hot_key_siblings=max(hot_sibling_counts, default=0),
                    avg_metadata_bytes=sum(metadata_bytes) / len(metadata_bytes) if metadata_bytes else 0.0,
                    avg_actor_entries=sum(actor_entries) / len(actor_entries) if actor_entries else 0.0,
                    avg_stale_actor_fraction=sum(stale_actor_fractions) / len(stale_actor_fractions)
                    if stale_actor_fractions
                    else 0.0,
                )
            self.env.schedule(self.sample_interval, snapshot)

        snapshot()

    def start(self) -> None:
        self._schedule_background_client()
        self._schedule_contention_burst()
        self._schedule_churn_event()
        self._schedule_burst_churn()
        self._schedule_snapshot()


def run_scenario(
    *,
    profile: str,
    clock_factory: Callable[[], ClockModel],
    sim_time: float,
    seed: int,
    initial_size: int,
    write_interval: float,
    max_nodes: int,
    min_nodes: int,
    min_lat: float,
    max_lat: float,
    key_count: int,
    hot_key_probability: float,
    client_count: int,
    replication_factor: int,
    sample_interval: float,
    lease_duration: float = 60.0,
    client_think_time: float = 4.0,
    merge_probability: float = 0.35,
    burst_interval: float = 18.0,
    burst_writers: int = 4,
    burst_spread: float = 2.0,
    merge_delay: float = 10.0,
    same_coordinator_probability: float = 0.75,
) -> MetricsCollector:
    random.seed(seed)
    env = Environment()
    metrics = MetricsCollector()
    workload = WorkloadConfig(
        key_count=key_count,
        hot_key_probability=hot_key_probability,
        client_count=client_count,
        write_interval=write_interval,
        client_think_time=client_think_time,
        merge_probability=merge_probability,
        burst_interval=burst_interval,
        burst_writers=burst_writers,
        burst_spread=burst_spread,
        merge_delay=merge_delay,
        same_coordinator_probability=same_coordinator_probability,
        replication_factor=replication_factor,
    )
    cluster = Cluster(
        env=env,
        metrics=metrics,
        clock_model=clock_factory(),
        profile=profile,
        initial_size=initial_size,
        max_nodes=max_nodes,
        min_nodes=min_nodes,
        min_lat=min_lat,
        max_lat=max_lat,
        sample_interval=sample_interval,
        workload=workload,
    )
    cluster.start()
    env.run(until=sim_time)
    return metrics


def save_run(
    metrics: MetricsCollector,
    *,
    output_dir: Path,
    run_name: str,
    config: dict[str, Any],
    sim_time: float,
) -> dict[str, Any]:
    summary = metrics.summary(sim_time)
    output_dir.mkdir(parents=True, exist_ok=True)
    metrics.save(output_dir, run_name)
    (output_dir / f"{run_name}_config.json").write_text(json.dumps(config, indent=2))
    (output_dir / f"{run_name}_summary.json").write_text(json.dumps(summary, indent=2))
    return summary


def make_clock_factory(clock_name: str, lease_duration: float) -> Callable[[], ClockModel]:
    if clock_name in {"vv", "vector"}:
        return VersionVectorModel
    if clock_name in {"vv_vnode", "vector_vnode"}:
        return VnodeVersionVectorModel
    if clock_name == "dvv":
        return DottedVersionVectorModel
    if clock_name == "lease_dvv":
        return lambda: LeaseDottedVersionVectorModel(lease_duration=lease_duration)
    raise KeyError(f"Unknown clock: {clock_name}")


CLOCK_FACTORIES: dict[str, Callable[[], ClockModel]] = {
    "dvv": DottedVersionVectorModel,
    "lease_dvv": lambda: LeaseDottedVersionVectorModel(lease_duration=60.0),
    "vector": VersionVectorModel,
    "vector_vnode": VnodeVersionVectorModel,
    "vv": VersionVectorModel,
    "vv_vnode": VnodeVersionVectorModel,
}


def build_parser() -> configargparse.ArgParser:
    parser = configargparse.ArgParser(
        description="Run the per-object causality simulator.",
        default_config_files=["configs/simulate.yaml"],
        config_file_parser_class=configargparse.YAMLConfigFileParser,
    )
    parser.add("-c", "--config", is_config_file=True, help="Path to a YAML config file.")
    parser.add_argument("--profile", choices=sorted(CHURN_PROFILES), default="sustained")
    parser.add_argument("--clock", choices=sorted(CLOCK_FACTORIES), default="dvv")
    parser.add_argument("--sim-time", type=float, default=240.0)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--initial-size", type=int, default=10)
    parser.add_argument("--write-interval", type=float, default=5.0)
    parser.add_argument("--client-think-time", type=float, default=4.0)
    parser.add_argument("--merge-probability", type=float, default=0.35)
    parser.add_argument("--burst-interval", type=float, default=18.0)
    parser.add_argument("--burst-writers", type=int, default=4)
    parser.add_argument("--burst-spread", type=float, default=2.0)
    parser.add_argument("--merge-delay", type=float, default=10.0)
    parser.add_argument("--same-coordinator-probability", type=float, default=0.75)
    parser.add_argument("--max-nodes", type=int, default=28)
    parser.add_argument("--min-nodes", type=int, default=4)
    parser.add_argument("--min-lat", type=float, default=1.0)
    parser.add_argument("--max-lat", type=float, default=5.0)
    parser.add_argument("--key-count", type=int, default=12)
    parser.add_argument("--hot-key-probability", type=float, default=0.65)
    parser.add_argument("--client-count", type=int, default=128)
    parser.add_argument("--replication-factor", type=int, default=4)
    parser.add_argument("--sample-interval", type=float, default=10.0)
    parser.add_argument("--lease-duration", type=float, default=16.0)
    parser.add_argument("--output-dir", default="output/runs")
    parser.add_argument("--run-name", default="clock_study")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    metrics = run_scenario(
        profile=args.profile,
        clock_factory=make_clock_factory(args.clock, args.lease_duration),
        sim_time=args.sim_time,
        seed=args.seed,
        initial_size=args.initial_size,
        write_interval=args.write_interval,
        max_nodes=args.max_nodes,
        min_nodes=args.min_nodes,
        min_lat=args.min_lat,
        max_lat=args.max_lat,
        key_count=args.key_count,
        hot_key_probability=args.hot_key_probability,
        client_count=args.client_count,
        replication_factor=args.replication_factor,
        sample_interval=args.sample_interval,
        lease_duration=args.lease_duration,
        client_think_time=args.client_think_time,
        merge_probability=args.merge_probability,
        burst_interval=args.burst_interval,
        burst_writers=args.burst_writers,
        burst_spread=args.burst_spread,
        merge_delay=args.merge_delay,
        same_coordinator_probability=args.same_coordinator_probability,
    )
    output_dir = Path(args.output_dir)
    config = {
        "profile": args.profile,
        "clock": args.clock,
        "sim_time": args.sim_time,
        "seed": args.seed,
        "initial_size": args.initial_size,
        "write_interval": args.write_interval,
        "client_think_time": args.client_think_time,
        "merge_probability": args.merge_probability,
        "burst_interval": args.burst_interval,
        "burst_writers": args.burst_writers,
        "burst_spread": args.burst_spread,
        "merge_delay": args.merge_delay,
        "same_coordinator_probability": args.same_coordinator_probability,
        "max_nodes": args.max_nodes,
        "min_nodes": args.min_nodes,
        "min_lat": args.min_lat,
        "max_lat": args.max_lat,
        "key_count": args.key_count,
        "hot_key_probability": args.hot_key_probability,
        "client_count": args.client_count,
        "replication_factor": args.replication_factor,
        "sample_interval": args.sample_interval,
        "lease_duration": args.lease_duration,
        "output_dir": args.output_dir,
        "run_name": args.run_name,
    }
    summary = save_run(
        metrics,
        output_dir=output_dir,
        run_name=args.run_name,
        config=config,
        sim_time=args.sim_time,
    )
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
