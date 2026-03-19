"""
Simulation harness for studying causal consistency under membership churn.

This version keeps the simulator responsible for recording raw event data and
lightweight summary statistics. A separate analysis step consumes the generated
CSVs to build plots and report tables.
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
from typing import Any, Callable

import configargparse


class Environment:
    """Minimal discrete-event simulation core."""

    def __init__(self):
        self._queue: list[tuple[float, int, Callable[[], None]]] = []
        self._seq = 0
        self.now = 0.0

    def schedule(self, delay: float, callback: Callable[[], None]) -> None:
        heapq.heappush(self._queue, (self.now + delay, self._seq, callback))
        self._seq += 1

    def run(self, until: float) -> None:
        while self._queue:
            t, _, cb = self._queue[0]
            if t > until:
                break
            heapq.heappop(self._queue)
            self.now = t
            cb()


class BaseClock(ABC):
    @abstractmethod
    def local_event(self, node_id: str, now: float) -> None:
        raise NotImplementedError

    @abstractmethod
    def prepare_send(self, node_id: str, now: float) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def update_on_receive(
        self,
        node_id: str,
        metadata: dict[str, Any],
        now: float,
    ) -> tuple[bool, bool]:
        raise NotImplementedError

    def prune_expired(self, now: float) -> set[str]:
        return set()

    @abstractmethod
    def metadata_size(self) -> int:
        raise NotImplementedError

    @abstractmethod
    def state_entries(self) -> set[str]:
        raise NotImplementedError

    @abstractmethod
    def state_payload(self) -> dict[str, Any]:
        raise NotImplementedError


class VectorClock(BaseClock):
    """Standard vector clock with a causal delivery check."""

    def __init__(self, initial: dict[str, int] | None = None):
        self.vc: dict[str, int] = defaultdict(int)
        if initial:
            self.vc.update(initial)

    def local_event(self, node_id: str, now: float) -> None:
        self.vc[node_id] += 1

    def prepare_send(self, node_id: str, now: float) -> dict[str, Any]:
        self.local_event(node_id, now)
        return dict(self.vc)

    def update_on_receive(
        self,
        node_id: str,
        metadata: dict[str, Any],
        now: float,
    ) -> tuple[bool, bool]:
        sender = metadata.get("__sender__")
        for key, value in metadata.items():
            if key == "__sender__":
                continue
            local_value = self.vc.get(key, 0)
            if key == sender:
                if value != local_value + 1:
                    return False, False
            elif value > local_value:
                return False, False

        for key, value in metadata.items():
            if key != "__sender__":
                self.vc[key] = max(self.vc.get(key, 0), value)
        return True, False

    def metadata_size(self) -> int:
        return len(self.vc)

    def state_entries(self) -> set[str]:
        return set(self.vc.keys())

    def state_payload(self) -> dict[str, Any]:
        return dict(self.vc)


class DottedVersionVectorClock(BaseClock):
    """Compact dotted version vector for causal broadcast delivery."""

    def __init__(self, initial: dict[str, int] | None = None):
        self.summary: dict[str, int] = defaultdict(int)
        if initial:
            self.summary.update(initial)

    def local_event(self, node_id: str, now: float) -> None:
        self.summary[node_id] += 1

    def prepare_send(self, node_id: str, now: float) -> dict[str, Any]:
        self.local_event(node_id, now)
        context = {
            key: value for key, value in self.summary.items() if key != node_id
        }
        dot = (node_id, self.summary[node_id])
        return {
            "__type__": "dvv",
            "__summary__": context,
            "__dot__": dot,
        }

    def update_on_receive(
        self,
        node_id: str,
        metadata: dict[str, Any],
        now: float,
    ) -> tuple[bool, bool]:
        summary = metadata.get("__summary__")
        dot = metadata.get("__dot__")
        sender = metadata.get("__sender__")

        if not isinstance(summary, dict) or not isinstance(dot, (tuple, list)) or len(dot) != 2:
            return False, False

        dot_node, dot_counter = dot
        if sender is None:
            sender = dot_node
        if dot_node != sender:
            return False, False
        if not isinstance(dot_counter, int):
            return False, False

        for key, value in summary.items():
            if not isinstance(value, int):
                return False, False
            local_value = self.summary.get(key, 0)
            if value > local_value:
                return False, False

        if self.summary.get(sender, 0) != dot_counter - 1:
            return False, False

        for key, value in summary.items():
            self.summary[key] = max(self.summary.get(key, 0), value)
        self.summary[sender] = max(self.summary.get(sender, 0), dot_counter)
        return True, False

    def metadata_size(self) -> int:
        return len(self.summary) + 1

    def state_entries(self) -> set[str]:
        return set(self.summary.keys())

    def state_payload(self) -> dict[str, Any]:
        return {
            "__type__": "dvv_state",
            "__summary__": dict(self.summary),
        }


class LeaseBasedDottedVersionVectorClock(DottedVersionVectorClock):
    """DVV with time-bounded summary entries pruned by simulation-time leases."""

    def __init__(
        self,
        lease_duration: float,
        initial: dict[str, int] | None = None,
    ):
        super().__init__(initial=initial)
        self.lease_duration = lease_duration
        self.expiries: dict[str, float] = {}

    def _renew_entry(self, node_id: str, now: float) -> None:
        self.expiries[node_id] = now + self.lease_duration

    def _collect_expired(self, now: float) -> set[str]:
        expired = {
            node_id
            for node_id, expiry in self.expiries.items()
            if expiry <= now and node_id in self.summary
        }
        for node_id in expired:
            self.summary.pop(node_id, None)
            self.expiries.pop(node_id, None)
        return expired

    def prune_expired(self, now: float) -> set[str]:
        return self._collect_expired(now)

    def local_event(self, node_id: str, now: float) -> None:
        self._collect_expired(now)
        self.summary[node_id] += 1
        self._renew_entry(node_id, now)

    def prepare_send(self, node_id: str, now: float) -> dict[str, Any]:
        expired_before_send = sorted(self._collect_expired(now))
        self.local_event(node_id, now)
        context = {
            key: value for key, value in self.summary.items() if key != node_id
        }
        dot = (node_id, self.summary[node_id])
        payload = {
            "__type__": "lease_dvv",
            "__summary__": context,
            "__dot__": dot,
        }
        if expired_before_send:
            payload["__pruned__"] = True
        return payload

    def update_on_receive(
        self,
        node_id: str,
        metadata: dict[str, Any],
        now: float,
    ) -> tuple[bool, bool]:
        locally_expired = self._collect_expired(now)
        delivered, _ = super().update_on_receive(node_id, metadata, now)
        if not delivered:
            return False, False

        dot = metadata.get("__dot__")
        sender = metadata.get("__sender__")
        pruned_context = bool(metadata.get("__pruned__"))

        if isinstance(dot, (tuple, list)) and len(dot) == 2:
            dot_node = str(dot[0])
            self._renew_entry(dot_node, now)
            if sender is None:
                sender = dot_node

        if sender is not None:
            self._renew_entry(str(sender), now)

        potential_violation = bool(pruned_context or locally_expired)
        return True, potential_violation

    def metadata_size(self) -> int:
        return len(self.summary) + 1

    def state_entries(self) -> set[str]:
        return set(self.summary.keys())

    def state_payload(self) -> dict[str, Any]:
        return {
            "__type__": "lease_dvv_state",
            "__summary__": dict(self.summary),
            "__leases__": dict(self.expiries),
        }


class FrontierClock(BaseClock):
    """Transmits only the new dot plus an immediate-dependency frontier."""

    def __init__(self, initial: dict[str, int] | None = None):
        self.summary: dict[str, int] = defaultdict(int)
        if initial:
            self.summary.update(initial)
        self.frontier: dict[str, int] = {}

    def local_event(self, node_id: str, now: float) -> None:
        self.summary[node_id] += 1

    def _validate_dependency_set(self, deps: dict[str, Any]) -> bool:
        for dep_node, dep_counter in deps.items():
            if not isinstance(dep_counter, int):
                return False
            if dep_counter > self.summary.get(str(dep_node), 0):
                return False
        return True

    def _refresh_frontier(self, dot_node: str, dot_counter: int, deps: dict[str, int]) -> None:
        for dep_node, dep_counter in deps.items():
            dep_node = str(dep_node)
            if self.frontier.get(dep_node, 0) <= dep_counter:
                self.frontier.pop(dep_node, None)

        if self.frontier.get(dot_node, 0) <= dot_counter:
            self.frontier.pop(dot_node, None)
        self.frontier[dot_node] = dot_counter

    def prepare_send(self, node_id: str, now: float) -> dict[str, Any]:
        deps = dict(self.frontier)
        self.local_event(node_id, now)
        dot_counter = self.summary[node_id]
        self._refresh_frontier(node_id, dot_counter, deps)
        return {
            "__type__": "frontier",
            "__frontier__": deps,
            "__dot__": (node_id, dot_counter),
        }

    def update_on_receive(
        self,
        node_id: str,
        metadata: dict[str, Any],
        now: float,
    ) -> tuple[bool, bool]:
        deps = metadata.get("__frontier__")
        dot = metadata.get("__dot__")
        sender = metadata.get("__sender__")

        if not isinstance(deps, dict) or not isinstance(dot, (tuple, list)) or len(dot) != 2:
            return False, False

        dot_node, dot_counter = dot
        dot_node = str(dot_node)
        if sender is None:
            sender = dot_node
        if dot_node != sender or not isinstance(dot_counter, int):
            return False, False
        if not self._validate_dependency_set(deps):
            return False, False
        if self.summary.get(dot_node, 0) != dot_counter - 1:
            return False, False

        self.summary[dot_node] = dot_counter
        self._refresh_frontier(dot_node, dot_counter, {str(k): int(v) for k, v in deps.items()})
        return True, False

    def metadata_size(self) -> int:
        return len(self.frontier) + 1

    def state_entries(self) -> set[str]:
        return set(self.summary.keys())

    def state_payload(self) -> dict[str, Any]:
        return {
            "__type__": "frontier_state",
            "__summary__": dict(self.summary),
            "__frontier__": dict(self.frontier),
        }


def metadata_size_for_message(metadata: dict[str, Any]) -> int:
    if "__summary__" in metadata and "__dot__" in metadata:
        summary = metadata.get("__summary__", {})
        if isinstance(summary, dict):
            return len(summary) + 1
        return 1
    if "__frontier__" in metadata and "__dot__" in metadata:
        frontier = metadata.get("__frontier__", {})
        if isinstance(frontier, dict):
            return len(frontier) + 1
        return 1
    return len(metadata) - int("__sender__" in metadata)


def encoded_size_bytes(payload: dict[str, Any]) -> int:
    return len(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def metadata_entries_for_message(metadata: dict[str, Any]) -> set[str]:
    if "__summary__" in metadata and "__dot__" in metadata:
        summary = metadata.get("__summary__", {})
        entries = set(summary.keys()) if isinstance(summary, dict) else set()
        dot = metadata.get("__dot__")
        if isinstance(dot, (tuple, list)) and len(dot) == 2:
            entries.add(str(dot[0]))
        return entries
    if "__frontier__" in metadata and "__dot__" in metadata:
        frontier = metadata.get("__frontier__", {})
        entries = set(frontier.keys()) if isinstance(frontier, dict) else set()
        dot = metadata.get("__dot__")
        if isinstance(dot, (tuple, list)) and len(dot) == 2:
            entries.add(str(dot[0]))
        return entries
    return {key for key in metadata.keys() if not key.startswith("__")}


def metadata_type_name(metadata: dict[str, Any]) -> str:
    if "__frontier__" in metadata and "__dot__" in metadata:
        return "frontier"
    if "__summary__" in metadata and "__dot__" in metadata:
        return str(metadata.get("__type__", "dvv"))
    return "vector"


def context_entry_count(metadata: dict[str, Any]) -> int:
    if "__frontier__" in metadata:
        frontier = metadata.get("__frontier__", {})
        return len(frontier) if isinstance(frontier, dict) else 0
    if "__summary__" in metadata:
        summary = metadata.get("__summary__", {})
        return len(summary) if isinstance(summary, dict) else 0
    metadata_entries = metadata_entries_for_message(metadata)
    return max(len(metadata_entries) - 1, 0)


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


class MetricsCollector:
    def __init__(self):
        self.sends: list[dict[str, Any]] = []
        self.deliveries: list[dict[str, Any]] = []
        self.joins: list[dict[str, Any]] = []
        self.leaves: list[dict[str, Any]] = []
        self.buffered: list[dict[str, Any]] = []
        self.queue_samples: list[dict[str, Any]] = []
        self.state_samples: list[dict[str, Any]] = []
        self.snapshot_samples: list[dict[str, Any]] = []
        self.sibling_events: list[dict[str, Any]] = []
        self.throughput_samples: list[dict[str, Any]] = []
        self.violations: list[dict[str, Any]] = []

    def record_send(
        self,
        node_id: str,
        key: str,
        meta_size: int,
        metadata_bytes: int,
        metadata_type: str,
        context_entries: int,
        stale_metadata_entries: int,
        stale_metadata_fraction: float,
        fanout: int,
        cluster_size: int,
        t: float,
    ) -> None:
        row = {
            "t": round_float(t),
            "node": node_id,
            "key": key,
            "meta_size": meta_size,
            "metadata_bytes": metadata_bytes,
            "metadata_type": metadata_type,
            "context_entries": context_entries,
            "stale_metadata_entries": stale_metadata_entries,
            "stale_metadata_fraction": round_float(stale_metadata_fraction),
            "fanout": fanout,
            "cluster_size": cluster_size,
        }
        self.sends.append(row)
        self.throughput_samples.append(
            {
                "t": row["t"],
                "event_type": "logical_write",
                "count": 1,
                "node": node_id,
            }
        )

    def record_delivery(
        self,
        sender: str,
        receiver: str,
        msg_id: int,
        key: str,
        latency: float,
        meta_size: int,
        t: float,
        delivered_from_buffer: bool,
    ) -> None:
        row = {
            "t": round_float(t),
            "sender": sender,
            "receiver": receiver,
            "msg_id": msg_id,
            "key": key,
            "latency": round_float(latency),
            "meta_size": meta_size,
            "delivered_from_buffer": int(delivered_from_buffer),
        }
        self.deliveries.append(row)
        self.throughput_samples.append(
            {
                "t": row["t"],
                "event_type": "delivery_message",
                "count": 1,
                "node": receiver,
            }
        )

    def record_buffered(self, node_id: str, msg_id: int, queue_len: int, t: float) -> None:
        self.buffered.append(
            {
                "t": round_float(t),
                "node": node_id,
                "msg_id": msg_id,
                "queue_len": queue_len,
            }
        )

    def record_queue_sample(
        self,
        node_id: str,
        queue_len: int,
        reason: str,
        t: float,
    ) -> None:
        self.queue_samples.append(
            {
                "t": round_float(t),
                "node": node_id,
                "queue_len": queue_len,
                "reason": reason,
            }
        )

    def record_state_sample(
        self,
        node_id: str,
        state_size: int,
        stale_state_entries: int,
        cluster_size: int,
        reason: str,
        t: float,
    ) -> None:
        stale_fraction = (
            stale_state_entries / state_size if state_size else 0.0
        )
        self.state_samples.append(
            {
                "t": round_float(t),
                "node": node_id,
                "state_size": state_size,
                "stale_state_entries": stale_state_entries,
                "stale_state_fraction": round_float(stale_fraction),
                "cluster_size": cluster_size,
                "reason": reason,
            }
        )

    def record_snapshot(
        self,
        t: float,
        active_nodes: int,
        avg_queue_len: float,
        max_queue_len: int,
        avg_state_size: float,
        max_state_size: int,
        avg_state_bytes: float,
        max_state_bytes: int,
        avg_stale_state_entries: float,
        avg_stale_state_fraction: float,
        avg_sibling_count: float,
        max_sibling_count: int,
        avg_hot_key_sibling_count: float,
    ) -> None:
        self.snapshot_samples.append(
            {
                "t": round_float(t),
                "active_nodes": active_nodes,
                "avg_queue_len": round_float(avg_queue_len),
                "max_queue_len": max_queue_len,
                "avg_state_size": round_float(avg_state_size),
                "max_state_size": max_state_size,
                "avg_state_bytes": round_float(avg_state_bytes),
                "max_state_bytes": max_state_bytes,
                "avg_stale_state_entries": round_float(avg_stale_state_entries),
                "avg_stale_state_fraction": round_float(avg_stale_state_fraction),
                "avg_sibling_count": round_float(avg_sibling_count),
                "max_sibling_count": max_sibling_count,
                "avg_hot_key_sibling_count": round_float(avg_hot_key_sibling_count),
            }
        )

    def record_sibling_event(
        self,
        node_id: str,
        key: str,
        sibling_count: int,
        relation: str,
        t: float,
    ) -> None:
        self.sibling_events.append(
            {
                "t": round_float(t),
                "node": node_id,
                "key": key,
                "sibling_count": sibling_count,
                "relation": relation,
            }
        )

    def record_violation(self, node_id: str, msg_id: int, t: float) -> None:
        self.violations.append(
            {"t": round_float(t), "node": node_id, "msg_id": msg_id}
        )

    def record_join(self, node_id: str, cluster_size: int, t: float) -> None:
        self.joins.append(
            {"t": round_float(t), "node": node_id, "cluster_size": cluster_size}
        )

    def record_leave(self, node_id: str, cluster_size: int, t: float) -> None:
        self.leaves.append(
            {"t": round_float(t), "node": node_id, "cluster_size": cluster_size}
        )

    def save(self, output_dir: Path, run_name: str) -> None:
        output_dir.mkdir(parents=True, exist_ok=True)
        for name, rows in [
            ("sends", self.sends),
            ("deliveries", self.deliveries),
            ("joins", self.joins),
            ("leaves", self.leaves),
            ("buffered", self.buffered),
            ("queue_samples", self.queue_samples),
            ("state_samples", self.state_samples),
            ("snapshot_samples", self.snapshot_samples),
            ("sibling_events", self.sibling_events),
            ("throughput_samples", self.throughput_samples),
            ("violations", self.violations),
        ]:
            if not rows:
                continue
            path = output_dir / f"{run_name}_{name}.csv"
            with path.open("w", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=rows[0].keys())
                writer.writeheader()
                writer.writerows(rows)

    def summary(self, sim_time: float) -> dict[str, Any]:
        send_meta = [row["meta_size"] for row in self.sends]
        send_meta_bytes = [row["metadata_bytes"] for row in self.sends]
        stale_meta = [row["stale_metadata_entries"] for row in self.sends]
        stale_meta_fraction = [row["stale_metadata_fraction"] for row in self.sends]
        delivery_latencies = [row["latency"] for row in self.deliveries]
        queue_lengths = [row["avg_queue_len"] for row in self.snapshot_samples]
        state_sizes = [row["avg_state_size"] for row in self.snapshot_samples]
        state_bytes = [row["avg_state_bytes"] for row in self.snapshot_samples]
        stale_state_entries = [row["avg_stale_state_entries"] for row in self.snapshot_samples]
        stale_state_fraction = [row["avg_stale_state_fraction"] for row in self.snapshot_samples]
        sibling_counts = [row["avg_sibling_count"] for row in self.snapshot_samples]
        hot_key_sibling_counts = [row["avg_hot_key_sibling_count"] for row in self.snapshot_samples]
        avg_logical_write_throughput = len(self.sends) / sim_time if sim_time else 0.0
        avg_delivery_message_throughput = (
            len(self.deliveries) / sim_time if sim_time else 0.0
        )

        return {
            "total_sends": len(self.sends),
            "total_deliveries": len(self.deliveries),
            "total_buffered": len(self.buffered),
            "causal_violations": len(self.violations),
            "joins": len(self.joins),
            "leaves": len(self.leaves),
            "avg_metadata_size": round_float(sum(send_meta) / len(send_meta) if send_meta else 0.0, 3),
            "max_metadata_size": max(send_meta, default=0),
            "metadata_size_p95": round_float(percentile(send_meta, 0.95), 3),
            "avg_metadata_bytes": round_float(
                sum(send_meta_bytes) / len(send_meta_bytes) if send_meta_bytes else 0.0,
                3,
            ),
            "p95_metadata_bytes": round_float(percentile(send_meta_bytes, 0.95), 3),
            "avg_stale_metadata_entries": round_float(
                sum(stale_meta) / len(stale_meta) if stale_meta else 0.0,
                3,
            ),
            "p95_stale_metadata_entries": round_float(percentile(stale_meta, 0.95), 3),
            "avg_stale_metadata_fraction": round_float(
                sum(stale_meta_fraction) / len(stale_meta_fraction)
                if stale_meta_fraction
                else 0.0,
                3,
            ),
            "avg_latency": round_float(
                sum(delivery_latencies) / len(delivery_latencies) if delivery_latencies else 0.0,
                3,
            ),
            "latency_p50": round_float(percentile(delivery_latencies, 0.50), 3),
            "latency_p95": round_float(percentile(delivery_latencies, 0.95), 3),
            "latency_p99": round_float(percentile(delivery_latencies, 0.99), 3),
            "sampled_avg_queue_len": round_float(
                sum(queue_lengths) / len(queue_lengths) if queue_lengths else 0.0,
                3,
            ),
            "sampled_max_queue_len": max(queue_lengths, default=0),
            "avg_state_size": round_float(
                sum(state_sizes) / len(state_sizes) if state_sizes else 0.0,
                3,
            ),
            "p95_state_size": round_float(percentile(state_sizes, 0.95), 3),
            "avg_state_bytes": round_float(
                sum(state_bytes) / len(state_bytes) if state_bytes else 0.0,
                3,
            ),
            "p95_state_bytes": round_float(percentile(state_bytes, 0.95), 3),
            "avg_stale_state_entries": round_float(
                sum(stale_state_entries) / len(stale_state_entries)
                if stale_state_entries
                else 0.0,
                3,
            ),
            "avg_stale_state_fraction": round_float(
                sum(stale_state_fraction) / len(stale_state_fraction)
                if stale_state_fraction
                else 0.0,
                3,
            ),
            "avg_sibling_count": round_float(
                sum(sibling_counts) / len(sibling_counts) if sibling_counts else 0.0,
                3,
            ),
            "p95_sibling_count": round_float(percentile(sibling_counts, 0.95), 3),
            "avg_hot_key_sibling_count": round_float(
                sum(hot_key_sibling_counts) / len(hot_key_sibling_counts)
                if hot_key_sibling_counts
                else 0.0,
                3,
            ),
            "avg_logical_write_throughput": round_float(
                avg_logical_write_throughput, 3
            ),
            "avg_delivery_message_throughput": round_float(
                avg_delivery_message_throughput, 3
            ),
        }


_MSG_ID = 0


def next_msg_id() -> int:
    global _MSG_ID
    _MSG_ID += 1
    return _MSG_ID


@dataclass
class Message:
    sender_id: str
    receiver_id: str
    key: str
    value: Any
    metadata: dict[str, Any]
    sent_at: float
    msg_id: int = field(default_factory=next_msg_id)


@dataclass
class VersionRecord:
    value: Any
    metadata: dict[str, Any]
    origin: str
    created_at: float
    msg_id: int | None = None


def metadata_frontier(metadata: dict[str, Any]) -> dict[str, int]:
    if "__summary__" in metadata and "__dot__" in metadata:
        summary = metadata.get("__summary__", {})
        frontier = dict(summary) if isinstance(summary, dict) else {}
        dot = metadata.get("__dot__")
        if isinstance(dot, (tuple, list)) and len(dot) == 2:
            frontier[str(dot[0])] = int(dot[1])
        return frontier
    if "__frontier__" in metadata and "__dot__" in metadata:
        frontier = metadata.get("__frontier__", {})
        materialized = dict(frontier) if isinstance(frontier, dict) else {}
        dot = metadata.get("__dot__")
        if isinstance(dot, (tuple, list)) and len(dot) == 2:
            materialized[str(dot[0])] = int(dot[1])
        return materialized
    return {
        key: int(value)
        for key, value in metadata.items()
        if not key.startswith("__")
    }


def compare_metadata(a: dict[str, Any], b: dict[str, Any]) -> str:
    a_frontier = metadata_frontier(a)
    b_frontier = metadata_frontier(b)
    keys = set(a_frontier) | set(b_frontier)
    a_ge_b = all(a_frontier.get(key, 0) >= b_frontier.get(key, 0) for key in keys)
    b_ge_a = all(b_frontier.get(key, 0) >= a_frontier.get(key, 0) for key in keys)
    a_gt_b = any(a_frontier.get(key, 0) > b_frontier.get(key, 0) for key in keys)
    b_gt_a = any(b_frontier.get(key, 0) > a_frontier.get(key, 0) for key in keys)

    if a_ge_b and a_gt_b:
        return "dominates"
    if b_ge_a and b_gt_a:
        return "dominated"
    if a_frontier == b_frontier:
        return "equal"
    return "concurrent"


class Node:
    def __init__(
        self,
        env: Environment,
        node_id: str,
        cluster: "Cluster",
        clock_factory: Callable[[], BaseClock],
        metrics: MetricsCollector,
        write_interval: float,
        min_lat: float,
        max_lat: float,
        key_count: int,
        hot_key_probability: float,
        replication_fanout: int,
    ):
        self.env = env
        self.id = node_id
        self.cluster = cluster
        self.clock = clock_factory()
        self.metrics = metrics
        self.write_interval = write_interval
        self.min_lat = min_lat
        self.max_lat = max_lat
        self.key_count = key_count
        self.hot_key_probability = hot_key_probability
        self.replication_fanout = replication_fanout
        self.kv: dict[str, list[VersionRecord]] = {}
        self.buffer: list[Message] = []
        self.active = True

    def start(self) -> None:
        self.metrics.record_queue_sample(self.id, 0, "start", self.env.now)
        self._record_state_sample("start")
        self._schedule_write()

    def stop(self) -> None:
        self.active = False
        self.metrics.record_queue_sample(self.id, len(self.buffer), "stop", self.env.now)
        self._record_state_sample("stop")

    def _record_state_sample(self, reason: str) -> None:
        self.clock.prune_expired(self.env.now)
        active_nodes = self.cluster.active_node_ids()
        stale_entries = len(self.clock.state_entries() - active_nodes)
        self.metrics.record_state_sample(
            node_id=self.id,
            state_size=len(self.clock.state_entries()),
            stale_state_entries=stale_entries,
            cluster_size=self.cluster.active_count(),
            reason=reason,
            t=self.env.now,
        )

    def _schedule_write(self) -> None:
        delay = random.expovariate(1.0 / self.write_interval)
        self.env.schedule(delay, self._do_write)

    def _choose_key(self) -> str:
        if self.key_count <= 1 or random.random() < self.hot_key_probability:
            return "k0"
        return f"k{random.randint(1, self.key_count - 1)}"

    def _apply_version(
        self,
        key: str,
        value: Any,
        metadata: dict[str, Any],
        origin: str,
        msg_id: int | None,
    ) -> int:
        versions = self.kv.setdefault(key, [])
        incoming = VersionRecord(
            value=value,
            metadata=copy.deepcopy(metadata),
            origin=origin,
            created_at=self.env.now,
            msg_id=msg_id,
        )
        kept_versions: list[VersionRecord] = []
        relation = "inserted"
        for existing in versions:
            ordering = compare_metadata(incoming.metadata, existing.metadata)
            if ordering == "dominates":
                relation = "dominates"
                continue
            if ordering in {"dominated", "equal"}:
                relation = ordering
                self.metrics.record_sibling_event(
                    self.id, key, len(versions), relation, self.env.now
                )
                return len(versions)
            kept_versions.append(existing)
        kept_versions.append(incoming)
        sibling_count = len(kept_versions)
        if sibling_count > 1 and relation == "inserted":
            relation = "concurrent"
        self.kv[key] = kept_versions
        self.metrics.record_sibling_event(
            self.id, key, sibling_count, relation, self.env.now
        )
        return sibling_count

    def _do_write(self) -> None:
        if not self.active:
            return

        key = self._choose_key()
        value = random.randint(0, 99)
        metadata = self.clock.prepare_send(self.id, self.env.now)
        metadata["__sender__"] = self.id
        self._apply_version(key, value, metadata, self.id, None)
        metadata_entries = metadata_entries_for_message(metadata)
        metadata_type = metadata_type_name(metadata)
        context_entries = context_entry_count(metadata)
        active_nodes = self.cluster.active_node_ids()

        peers = self.cluster.active_peers(self.id, self.replication_fanout)
        self.metrics.record_send(
            node_id=self.id,
            key=key,
            meta_size=metadata_size_for_message(metadata),
            metadata_bytes=encoded_size_bytes(metadata),
            metadata_type=metadata_type,
            context_entries=context_entries,
            stale_metadata_entries=len(metadata_entries - active_nodes),
            stale_metadata_fraction=(
                len(metadata_entries - active_nodes) / len(metadata_entries)
                if metadata_entries
                else 0.0
            ),
            fanout=len(peers),
            cluster_size=self.cluster.active_count(),
            t=self.env.now,
        )
        self._record_state_sample("send")

        for peer in peers:
            message = Message(
                sender_id=self.id,
                receiver_id=peer.id,
                key=key,
                value=value,
                metadata=copy.deepcopy(metadata),
                sent_at=self.env.now,
            )
            delay = random.uniform(self.min_lat, self.max_lat)
            self.env.schedule(delay, self._make_deliver(peer, message))

        self._schedule_write()

    def _make_deliver(self, peer: "Node", message: Message) -> Callable[[], None]:
        def deliver() -> None:
            if peer.active:
                peer._receive(message)

        return deliver

    def _receive(self, msg: Message) -> None:
        delivered, potential_violation = self.clock.update_on_receive(
            self.id,
            msg.metadata,
            self.env.now,
        )
        if delivered:
            self._apply_version(msg.key, msg.value, msg.metadata, msg.sender_id, msg.msg_id)
            self.metrics.record_delivery(
                sender=msg.sender_id,
                receiver=self.id,
                msg_id=msg.msg_id,
                key=msg.key,
                latency=self.env.now - msg.sent_at,
                meta_size=metadata_size_for_message(msg.metadata),
                t=self.env.now,
                delivered_from_buffer=False,
            )
            if potential_violation:
                self.metrics.record_violation(self.id, msg.msg_id, self.env.now)
            self._record_state_sample("receive")
            self._retry_buffer()
            return

        self.buffer.append(msg)
        self.metrics.record_buffered(self.id, msg.msg_id, len(self.buffer), self.env.now)
        self.metrics.record_queue_sample(self.id, len(self.buffer), "buffered", self.env.now)

    def _retry_buffer(self) -> None:
        changed = True
        while changed:
            changed = False
            still_blocked: list[Message] = []
            for msg in self.buffer:
                delivered, potential_violation = self.clock.update_on_receive(
                    self.id,
                    msg.metadata,
                    self.env.now,
                )
                if delivered:
                    self._apply_version(msg.key, msg.value, msg.metadata, msg.sender_id, msg.msg_id)
                    self.metrics.record_delivery(
                        sender=msg.sender_id,
                        receiver=self.id,
                        msg_id=msg.msg_id,
                        key=msg.key,
                        latency=self.env.now - msg.sent_at,
                        meta_size=metadata_size_for_message(msg.metadata),
                        t=self.env.now,
                        delivered_from_buffer=True,
                    )
                    if potential_violation:
                        self.metrics.record_violation(self.id, msg.msg_id, self.env.now)
                    self._record_state_sample("retry_receive")
                    changed = True
                else:
                    still_blocked.append(msg)
            if len(still_blocked) != len(self.buffer):
                self.metrics.record_queue_sample(
                    self.id,
                    len(still_blocked),
                    "retry",
                    self.env.now,
                )
            self.buffer = still_blocked


CHURN_PROFILES = {
    "stable": {"join_rate": 0.0, "leave_rate": 0.0, "burst_size": 0, "burst_interval": None},
    "low": {"join_rate": 0.02, "leave_rate": 0.02, "burst_size": 0, "burst_interval": None},
    "sustained": {"join_rate": 0.06, "leave_rate": 0.06, "burst_size": 0, "burst_interval": None},
    "burst": {"join_rate": 0.02, "leave_rate": 0.02, "burst_size": 8, "burst_interval": 40.0},
}


class Cluster:
    def __init__(
        self,
        env: Environment,
        metrics: MetricsCollector,
        initial_size: int,
        clock_factory: Callable[[], BaseClock],
        profile: str,
        max_nodes: int,
        min_nodes: int,
        write_interval: float,
        min_lat: float,
        max_lat: float,
        key_count: int,
        hot_key_probability: float,
        replication_fanout: int,
        sample_interval: float,
        lease_duration: float,
    ):
        self.env = env
        self.metrics = metrics
        self.clock_factory = clock_factory
        self.max_nodes = max_nodes
        self.min_nodes = min_nodes
        self.write_interval = write_interval
        self.min_lat = min_lat
        self.max_lat = max_lat
        self.key_count = key_count
        self.hot_key_probability = hot_key_probability
        self.replication_fanout = replication_fanout
        self.sample_interval = sample_interval
        self.lease_duration = lease_duration
        self.profile = CHURN_PROFILES[profile]
        self.nodes: list[Node] = []
        self.counter = 0

        for _ in range(initial_size):
            self._add_node()

    def active_peers(self, exclude_id: str, fanout: int | None = None) -> list[Node]:
        peers = [node for node in self.nodes if node.active and node.id != exclude_id]
        if fanout is None or fanout <= 0 or fanout >= len(peers):
            return peers
        return random.sample(peers, fanout)

    def active_node_ids(self) -> set[str]:
        return {node.id for node in self.nodes if node.active}

    def active_count(self) -> int:
        return sum(1 for node in self.nodes if node.active)

    def record_snapshot(self) -> None:
        active_nodes = [node for node in self.nodes if node.active]
        for node in active_nodes:
            node.clock.prune_expired(self.env.now)
        active_node_ids = {node.id for node in active_nodes}
        if not active_nodes:
            self.metrics.record_snapshot(
                t=self.env.now,
                active_nodes=0,
                avg_queue_len=0.0,
                max_queue_len=0,
                avg_state_size=0.0,
                max_state_size=0,
                avg_state_bytes=0.0,
                max_state_bytes=0,
                avg_stale_state_entries=0.0,
                avg_stale_state_fraction=0.0,
                avg_sibling_count=0.0,
                max_sibling_count=0,
                avg_hot_key_sibling_count=0.0,
            )
            return

        queue_lengths = [len(node.buffer) for node in active_nodes]
        state_sizes = [len(node.clock.state_entries()) for node in active_nodes]
        state_bytes = [
            encoded_size_bytes(node.clock.state_payload()) for node in active_nodes
        ]
        stale_state_entries = [
            len(node.clock.state_entries() - active_node_ids) for node in active_nodes
        ]
        stale_state_fractions = [
            (stale / size) if size else 0.0
            for stale, size in zip(stale_state_entries, state_sizes)
        ]
        sibling_counts = [
            count
            for node in active_nodes
            for count in [len(versions) for versions in node.kv.values()]
        ]
        hot_key_sibling_counts = [
            len(node.kv.get("k0", [])) for node in active_nodes if "k0" in node.kv
        ]
        self.metrics.record_snapshot(
            t=self.env.now,
            active_nodes=len(active_nodes),
            avg_queue_len=sum(queue_lengths) / len(queue_lengths),
            max_queue_len=max(queue_lengths, default=0),
            avg_state_size=sum(state_sizes) / len(state_sizes),
            max_state_size=max(state_sizes, default=0),
            avg_state_bytes=sum(state_bytes) / len(state_bytes),
            max_state_bytes=max(state_bytes, default=0),
            avg_stale_state_entries=sum(stale_state_entries) / len(stale_state_entries),
            avg_stale_state_fraction=sum(stale_state_fractions) / len(stale_state_fractions),
            avg_sibling_count=sum(sibling_counts) / len(sibling_counts)
            if sibling_counts
            else 0.0,
            max_sibling_count=max(sibling_counts, default=0),
            avg_hot_key_sibling_count=sum(hot_key_sibling_counts) / len(hot_key_sibling_counts)
            if hot_key_sibling_counts
            else 0.0,
        )

    def start_sampling(self) -> None:
        self.record_snapshot()
        self._schedule_snapshot()

    def _schedule_snapshot(self) -> None:
        def take_snapshot() -> None:
            self.record_snapshot()
            self._schedule_snapshot()

        self.env.schedule(self.sample_interval, take_snapshot)

    def _add_node(self) -> None:
        self.counter += 1
        node_id = f"n{self.counter:04d}"
        node = Node(
            env=self.env,
            node_id=node_id,
            cluster=self,
            clock_factory=self.clock_factory,
            metrics=self.metrics,
            write_interval=self.write_interval,
            min_lat=self.min_lat,
            max_lat=self.max_lat,
            key_count=self.key_count,
            hot_key_probability=self.hot_key_probability,
            replication_fanout=self.replication_fanout,
        )
        self.nodes.append(node)
        node.start()
        self.metrics.record_join(node_id, self.active_count(), self.env.now)

    def _remove_node(self) -> None:
        active = [node for node in self.nodes if node.active]
        if len(active) <= self.min_nodes:
            return
        victim = random.choice(active)
        victim.stop()
        self.metrics.record_leave(victim.id, self.active_count(), self.env.now)

    def start_churn(self) -> None:
        join_rate = self.profile["join_rate"]
        leave_rate = self.profile["leave_rate"]
        burst_size = self.profile["burst_size"]
        burst_interval = self.profile["burst_interval"]
        if join_rate + leave_rate > 0:
            self._schedule_churn_event(join_rate, leave_rate)
        if burst_size > 0 and burst_interval is not None:
            self.env.schedule(burst_interval, lambda: self._burst_event(burst_size, burst_interval))

    def _schedule_churn_event(self, join_rate: float, leave_rate: float) -> None:
        total_rate = join_rate + leave_rate
        delay = random.expovariate(total_rate)

        def do_churn() -> None:
            if random.random() < join_rate / total_rate:
                if self.active_count() < self.max_nodes:
                    self._add_node()
            else:
                self._remove_node()
            self._schedule_churn_event(join_rate, leave_rate)

        self.env.schedule(delay, do_churn)

    def _burst_event(self, burst_size: int, burst_interval: float) -> None:
        for _ in range(burst_size):
            self._remove_node()

        def rejoin() -> None:
            for _ in range(burst_size):
                if self.active_count() < self.max_nodes:
                    self._add_node()
            self.env.schedule(burst_interval, lambda: self._burst_event(burst_size, burst_interval))

        self.env.schedule(burst_interval / 2, rejoin)


def run_scenario(
    profile: str,
    clock_factory: Callable[[], BaseClock],
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
    replication_fanout: int,
    sample_interval: float,
    lease_duration: float = 60.0,
) -> MetricsCollector:
    global _MSG_ID
    _MSG_ID = 0
    random.seed(seed)
    env = Environment()
    metrics = MetricsCollector()
    cluster = Cluster(
        env=env,
        metrics=metrics,
        initial_size=initial_size,
        clock_factory=clock_factory,
        profile=profile,
        max_nodes=max_nodes,
        min_nodes=min_nodes,
        write_interval=write_interval,
        min_lat=min_lat,
        max_lat=max_lat,
        key_count=key_count,
        hot_key_probability=hot_key_probability,
        replication_fanout=replication_fanout,
        sample_interval=sample_interval,
        lease_duration=lease_duration,
    )
    cluster.start_churn()
    cluster.start_sampling()
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


def make_clock_factory(clock_name: str, lease_duration: float) -> Callable[[], BaseClock]:
    if clock_name == "vector":
        return VectorClock
    if clock_name == "dvv":
        return DottedVersionVectorClock
    if clock_name == "frontier":
        return FrontierClock
    if clock_name == "lease_dvv":
        return lambda: LeaseBasedDottedVersionVectorClock(lease_duration=lease_duration)
    raise KeyError(f"Unknown clock: {clock_name}")


CLOCK_FACTORIES: dict[str, Callable[[], BaseClock]] = {
    "dvv": DottedVersionVectorClock,
    "frontier": FrontierClock,
    "lease_dvv": lambda: LeaseBasedDottedVersionVectorClock(lease_duration=60.0),
    "vector": VectorClock,
}


def build_parser() -> configargparse.ArgParser:
    parser = configargparse.ArgParser(
        description="Run the churn simulator.",
        default_config_files=["configs/simulate.yaml"],
        config_file_parser_class=configargparse.YAMLConfigFileParser,
    )
    parser.add(
        "-c",
        "--config",
        is_config_file=True,
        help="Path to a YAML config file.",
    )
    parser.add_argument("--profile", choices=sorted(CHURN_PROFILES), default="sustained")
    parser.add_argument("--clock", choices=sorted(CLOCK_FACTORIES), default="vector")
    parser.add_argument("--sim-time", type=float, default=300.0)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--initial-size", type=int, default=15)
    parser.add_argument("--write-interval", type=float, default=20.0)
    parser.add_argument("--max-nodes", type=int, default=40)
    parser.add_argument("--min-nodes", type=int, default=5)
    parser.add_argument("--min-lat", type=float, default=1.0)
    parser.add_argument("--max-lat", type=float, default=5.0)
    parser.add_argument("--key-count", type=int, default=5)
    parser.add_argument("--hot-key-probability", type=float, default=0.8)
    parser.add_argument("--replication-fanout", type=int, default=0)
    parser.add_argument("--sample-interval", type=float, default=20.0)
    parser.add_argument("--lease-duration", type=float, default=60.0)
    parser.add_argument("--output-dir", default="output/runs")
    parser.add_argument("--run-name", default="run")
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
        replication_fanout=args.replication_fanout,
        sample_interval=args.sample_interval,
        lease_duration=args.lease_duration,
    )

    output_dir = Path(args.output_dir)
    config = {
        "profile": args.profile,
        "clock": args.clock,
        "sim_time": args.sim_time,
        "seed": args.seed,
        "initial_size": args.initial_size,
        "write_interval": args.write_interval,
        "max_nodes": args.max_nodes,
        "min_nodes": args.min_nodes,
        "min_lat": args.min_lat,
        "max_lat": args.max_lat,
        "key_count": args.key_count,
        "hot_key_probability": args.hot_key_probability,
        "replication_fanout": args.replication_fanout,
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
