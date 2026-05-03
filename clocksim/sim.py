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

import heapq
import json
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .metrics import MetricsCollector

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


from .clocks import ClockModel
from .context import Dot, EventId
from .store import Message, Node, VersionRecord

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
        represented = {
            EventId.from_dot(version.key, dot)
            for dot in version.stamp.represented_context().materialize()
        }
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
        true_history: set[EventId] = {EventId.from_dot(key, stamp.dot)}
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



