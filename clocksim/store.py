"""Storage records, replication messages, and node apply semantics."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .clocks import BaseStamp, ClockModel
from .context import Dot, EventId
from .metrics import MetricsCollector

if TYPE_CHECKING:
    from .sim import Cluster, Environment


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
    true_history: set[EventId]

    @property
    def dot(self) -> Dot:
        return self.stamp.dot





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

