"""Metrics collection and summary helpers."""

from __future__ import annotations

import csv
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .store import VersionRecord


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


@dataclass(frozen=True)
class WriteMetric:
    t: float
    version_id: str
    node: str
    actor_id: str
    key: str
    phase: str
    read_size: int
    metadata_type: str
    metadata_bytes: int
    metadata_components: int
    actor_entries: int
    represented_events: int
    true_events: int
    pruned_actors: int
    pruned_events: int
    was_pruned: int
    target_replicas: int
    is_hot_key: int
    adaptive_lease_min: float
    adaptive_lease_avg: float
    adaptive_lease_max: float


@dataclass(frozen=True)
class AccuracyMetric:
    t: float
    version_id: str
    key: str
    node: str
    precision: float
    recall: float
    false_positive_events: int
    false_negative_events: int
    was_pruned: int
    is_hot_key: int


@dataclass(frozen=True)
class DeliveryMetric:
    t: float
    version_id: str
    key: str
    sender: str
    receiver: str
    latency: float
    action: str
    sibling_count_after: int


@dataclass(frozen=True)
class DecisionMetric:
    t: float
    node: str
    key: str
    incoming_id: str
    existing_id: str
    true_relation: str
    clock_relation: str
    final_action: str
    is_hot_key: int


@dataclass(frozen=True)
class SnapshotMetric:
    t: float
    active_nodes: int
    avg_versions_per_key: float
    avg_hot_key_siblings: float
    max_hot_key_siblings: int
    avg_metadata_bytes: float
    avg_actor_entries: float
    avg_sibling_set_metadata_bytes: float
    avg_sibling_set_metadata_components: float
    avg_stale_actor_fraction: float
    configured_join_rate: float = 0.0
    configured_leave_rate: float = 0.0
    configured_churn_rate: float = 0.0


@dataclass(frozen=True)
class MembershipMetric:
    t: float
    node: str
    cluster_size: int


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
        adaptive_lease_min: float = 0.0,
        adaptive_lease_avg: float = 0.0,
        adaptive_lease_max: float = 0.0,
    ) -> None:
        self.writes.append(
            asdict(
                WriteMetric(
                    t=round_float(t),
                    version_id=version.version_id,
                    node=node_id,
                    actor_id=actor_id,
                    key=version.key,
                    phase=version.phase,
                    read_size=version.read_size,
                    metadata_type=version.stamp.serialize()["type"],
                    metadata_bytes=metadata_bytes,
                    metadata_components=metadata_components,
                    actor_entries=actor_entries,
                    represented_events=represented_events,
                    true_events=true_events,
                    pruned_actors=version.stamp.pruned_actor_count(),
                    pruned_events=version.stamp.pruned_event_count(),
                    was_pruned=int(version.stamp.was_pruned()),
                    target_replicas=target_replicas,
                    is_hot_key=int(is_hot_key),
                    adaptive_lease_min=round_float(adaptive_lease_min),
                    adaptive_lease_avg=round_float(adaptive_lease_avg),
                    adaptive_lease_max=round_float(adaptive_lease_max),
                )
            )
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
            asdict(
                AccuracyMetric(
                    t=round_float(t),
                    version_id=version.version_id,
                    key=version.key,
                    node=version.origin,
                    precision=round_float(precision),
                    recall=round_float(recall),
                    false_positive_events=false_positive_events,
                    false_negative_events=false_negative_events,
                    was_pruned=int(version.stamp.was_pruned()),
                    is_hot_key=int(is_hot_key),
                )
            )
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
            asdict(
                DeliveryMetric(
                    t=round_float(t),
                    version_id=version_id,
                    key=key,
                    sender=sender,
                    receiver=receiver,
                    latency=round_float(latency),
                    action=action,
                    sibling_count_after=sibling_count_after,
                )
            )
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
            asdict(
                DecisionMetric(
                    t=round_float(t),
                    node=node_id,
                    key=key,
                    incoming_id=incoming_id,
                    existing_id=existing_id,
                    true_relation=true_relation,
                    clock_relation=clock_relation,
                    final_action=final_action,
                    is_hot_key=int(is_hot_key),
                )
            )
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
        avg_sibling_set_metadata_bytes: float,
        avg_sibling_set_metadata_components: float,
        avg_stale_actor_fraction: float,
        configured_join_rate: float = 0.0,
        configured_leave_rate: float = 0.0,
        configured_churn_rate: float = 0.0,
    ) -> None:
        self.snapshots.append(
            asdict(
                SnapshotMetric(
                    t=round_float(t),
                    active_nodes=active_nodes,
                    avg_versions_per_key=round_float(avg_versions_per_key),
                    avg_hot_key_siblings=round_float(avg_hot_key_siblings),
                    max_hot_key_siblings=max_hot_key_siblings,
                    avg_metadata_bytes=round_float(avg_metadata_bytes),
                    avg_actor_entries=round_float(avg_actor_entries),
                    avg_sibling_set_metadata_bytes=round_float(
                        avg_sibling_set_metadata_bytes
                    ),
                    avg_sibling_set_metadata_components=round_float(
                        avg_sibling_set_metadata_components
                    ),
                    avg_stale_actor_fraction=round_float(avg_stale_actor_fraction),
                    configured_join_rate=round_float(configured_join_rate),
                    configured_leave_rate=round_float(configured_leave_rate),
                    configured_churn_rate=round_float(configured_churn_rate),
                )
            )
        )

    def record_join(self, node_id: str, cluster_size: int, t: float) -> None:
        self.joins.append(
            asdict(
                MembershipMetric(
                    t=round_float(t),
                    node=node_id,
                    cluster_size=cluster_size,
                )
            )
        )

    def record_leave(self, node_id: str, cluster_size: int, t: float) -> None:
        self.leaves.append(
            asdict(
                MembershipMetric(
                    t=round_float(t),
                    node=node_id,
                    cluster_size=cluster_size,
                )
            )
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
        adaptive_lease_avg = [
            float(row.get("adaptive_lease_avg", 0.0))
            for row in self.writes
            if float(row.get("adaptive_lease_avg", 0.0)) > 0.0
        ]
        precision = [float(row["precision"]) for row in self.accuracy]
        recall = [float(row["recall"]) for row in self.accuracy]
        stale_actor_fraction = [
            float(row["avg_stale_actor_fraction"]) for row in self.snapshots
        ]
        sibling_set_metadata_bytes = [
            float(
                row.get(
                    "avg_sibling_set_metadata_bytes",
                    row.get("avg_metadata_bytes", 0.0),
                )
            )
            for row in self.snapshots
        ]
        sibling_set_metadata_components = [
            float(row.get("avg_sibling_set_metadata_components", 0.0))
            for row in self.snapshots
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
            if row["clock_relation"] in {"dominates", "dominated", "equal"}
            and row["final_action"] in {"drop_existing", "drop_incoming"}
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
            "avg_adaptive_lease": round_float(
                sum(adaptive_lease_avg) / len(adaptive_lease_avg),
                3,
            )
            if adaptive_lease_avg
            else 0.0,
            "avg_actor_entries": round_float(sum(actor_entries) / len(actor_entries), 3)
            if actor_entries
            else 0.0,
            "avg_sibling_set_metadata_bytes": round_float(
                sum(sibling_set_metadata_bytes) / len(sibling_set_metadata_bytes),
                3,
            )
            if sibling_set_metadata_bytes
            else 0.0,
            "avg_sibling_set_metadata_components": round_float(
                sum(sibling_set_metadata_components) / len(sibling_set_metadata_components),
                3,
            )
            if sibling_set_metadata_components
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
