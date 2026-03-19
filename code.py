"""
Simulation harness for studying causal consistency under membership churn.

This version keeps the simulator responsible for recording raw event data and
lightweight summary statistics. A separate analysis step consumes the generated
CSVs to build plots and report tables.
"""

from __future__ import annotations

import argparse
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
    def local_event(self, node_id: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def prepare_send(self, node_id: str) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def update_on_receive(self, node_id: str, metadata: dict[str, Any]) -> bool:
        raise NotImplementedError

    @abstractmethod
    def metadata_size(self) -> int:
        raise NotImplementedError


class VectorClock(BaseClock):
    """Standard vector clock with a causal delivery check."""

    def __init__(self, initial: dict[str, int] | None = None):
        self.vc: dict[str, int] = defaultdict(int)
        if initial:
            self.vc.update(initial)

    def local_event(self, node_id: str) -> None:
        self.vc[node_id] += 1

    def prepare_send(self, node_id: str) -> dict[str, Any]:
        self.local_event(node_id)
        return dict(self.vc)

    def update_on_receive(self, node_id: str, metadata: dict[str, Any]) -> bool:
        sender = metadata.get("__sender__")
        for key, value in metadata.items():
            if key == "__sender__":
                continue
            local_value = self.vc.get(key, 0)
            if key == sender:
                if value != local_value + 1:
                    return False
            elif value > local_value:
                return False

        for key, value in metadata.items():
            if key != "__sender__":
                self.vc[key] = max(self.vc.get(key, 0), value)
        return True

    def metadata_size(self) -> int:
        return len(self.vc)


class DottedVersionVectorClock(BaseClock):
    """Compact dotted version vector for causal broadcast delivery."""

    def __init__(self, initial: dict[str, int] | None = None):
        self.summary: dict[str, int] = defaultdict(int)
        if initial:
            self.summary.update(initial)

    def local_event(self, node_id: str) -> None:
        self.summary[node_id] += 1

    def prepare_send(self, node_id: str) -> dict[str, Any]:
        context = dict(self.summary)
        self.local_event(node_id)
        dot = (node_id, self.summary[node_id])
        return {
            "__type__": "dvv",
            "__summary__": context,
            "__dot__": dot,
        }

    def update_on_receive(self, node_id: str, metadata: dict[str, Any]) -> bool:
        summary = metadata.get("__summary__")
        dot = metadata.get("__dot__")
        sender = metadata.get("__sender__")

        if not isinstance(summary, dict) or not isinstance(dot, (tuple, list)) or len(dot) != 2:
            return False

        dot_node, dot_counter = dot
        if sender is None:
            sender = dot_node
        if dot_node != sender:
            return False
        if not isinstance(dot_counter, int):
            return False

        for key, value in summary.items():
            if not isinstance(value, int):
                return False
            local_value = self.summary.get(key, 0)
            if key == sender:
                if value > local_value:
                    return False
            elif value > local_value:
                return False

        if dot_counter != summary.get(sender, 0) + 1:
            return False

        if self.summary.get(sender, 0) != dot_counter - 1:
            return False

        for key, value in summary.items():
            self.summary[key] = max(self.summary.get(key, 0), value)
        self.summary[sender] = max(self.summary.get(sender, 0), dot_counter)
        return True

    def metadata_size(self) -> int:
        return len(self.summary) + 1


def metadata_size_for_message(metadata: dict[str, Any]) -> int:
    if "__summary__" in metadata and "__dot__" in metadata:
        summary = metadata.get("__summary__", {})
        if isinstance(summary, dict):
            return len(summary) + 1
        return 1
    return len(metadata) - int("__sender__" in metadata)


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
        self.throughput_samples: list[dict[str, Any]] = []
        self.violations: list[dict[str, Any]] = []

    def record_send(
        self,
        node_id: str,
        key: str,
        meta_size: int,
        fanout: int,
        cluster_size: int,
        t: float,
    ) -> None:
        row = {
            "t": round_float(t),
            "node": node_id,
            "key": key,
            "meta_size": meta_size,
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
        delivery_latencies = [row["latency"] for row in self.deliveries]
        queue_lengths = [row["queue_len"] for row in self.queue_samples]
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
        self.kv: dict[str, Any] = {}
        self.buffer: list[Message] = []
        self.active = True

    def start(self) -> None:
        self.metrics.record_queue_sample(self.id, 0, "start", self.env.now)
        self._schedule_write()

    def stop(self) -> None:
        self.active = False
        self.metrics.record_queue_sample(self.id, len(self.buffer), "stop", self.env.now)

    def _schedule_write(self) -> None:
        delay = random.expovariate(1.0 / self.write_interval)
        self.env.schedule(delay, self._do_write)

    def _do_write(self) -> None:
        if not self.active:
            return

        key = f"k{random.randint(0, self.key_count - 1)}"
        value = random.randint(0, 99)
        metadata = self.clock.prepare_send(self.id)
        metadata["__sender__"] = self.id
        self.kv[key] = value

        peers = self.cluster.active_peers(self.id)
        self.metrics.record_send(
            node_id=self.id,
            key=key,
            meta_size=self.clock.metadata_size(),
            fanout=len(peers),
            cluster_size=self.cluster.active_count(),
            t=self.env.now,
        )

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
        if self.clock.update_on_receive(self.id, msg.metadata):
            self.kv[msg.key] = msg.value
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
                if self.clock.update_on_receive(self.id, msg.metadata):
                    self.kv[msg.key] = msg.value
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
    "low": {"join_rate": 0.01, "leave_rate": 0.01, "burst_size": 0, "burst_interval": None},
    "sustained": {"join_rate": 0.03, "leave_rate": 0.03, "burst_size": 0, "burst_interval": None},
    "burst": {"join_rate": 0.005, "leave_rate": 0.005, "burst_size": 5, "burst_interval": 60.0},
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
        self.profile = CHURN_PROFILES[profile]
        self.nodes: list[Node] = []
        self.counter = 0

        for _ in range(initial_size):
            self._add_node()

    def active_peers(self, exclude_id: str) -> list[Node]:
        return [node for node in self.nodes if node.active and node.id != exclude_id]

    def active_count(self) -> int:
        return sum(1 for node in self.nodes if node.active)

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
    )
    cluster.start_churn()
    env.run(until=sim_time)
    return metrics


CLOCK_FACTORIES: dict[str, Callable[[], BaseClock]] = {
    "dvv": DottedVersionVectorClock,
    "vector": VectorClock,
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the churn simulator.")
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
    parser.add_argument("--output-dir", default="out")
    parser.add_argument("--run-name", default="run")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    metrics = run_scenario(
        profile=args.profile,
        clock_factory=CLOCK_FACTORIES[args.clock],
        sim_time=args.sim_time,
        seed=args.seed,
        initial_size=args.initial_size,
        write_interval=args.write_interval,
        max_nodes=args.max_nodes,
        min_nodes=args.min_nodes,
        min_lat=args.min_lat,
        max_lat=args.max_lat,
        key_count=args.key_count,
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
        "output_dir": args.output_dir,
        "run_name": args.run_name,
    }

    summary = metrics.summary(args.sim_time)
    output_dir.mkdir(parents=True, exist_ok=True)
    metrics.save(output_dir, args.run_name)
    (output_dir / f"{args.run_name}_config.json").write_text(json.dumps(config, indent=2))
    (output_dir / f"{args.run_name}_summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
