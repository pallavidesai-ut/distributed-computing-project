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
import bisect
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable

from .config import CHURN_PROFILES, ClusterConfig, NetworkConfig, ScenarioConfig, WorkloadConfig, scenario_config_from_kwargs
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

    def run(self, until: float, *, progress: bool = False, desc: str | None = None) -> None:
        progress_bar = None
        if progress:
            try:
                from tqdm.auto import tqdm
            except ImportError as exc:  # pragma: no cover - dependency/configuration guard
                raise RuntimeError("Progress display requires tqdm. Install project dependencies or omit --progress.") from exc
            progress_bar = tqdm(
                total=until,
                initial=min(self.now, until),
                desc=desc or "simulation",
                unit="sim-time",
            )

        try:
            while self._queue:
                t, _, callback = self._queue[0]
                if t > until:
                    if progress_bar is not None:
                        progress_bar.update(max(0.0, until - progress_bar.n))
                    break
                heapq.heappop(self._queue)
                self.now = t
                if progress_bar is not None:
                    progress_bar.update(max(0.0, min(self.now, until) - progress_bar.n))
                callback()
        finally:
            if progress_bar is not None:
                progress_bar.close()


from .clocks import ClockModel
from .context import Dot, EventId
from .store import Message, Node, VersionRecord

class Cluster:
    def __init__(
        self,
        *,
        env: Environment,
        metrics: MetricsCollector,
        clock_model: ClockModel,
        config: ScenarioConfig | None = None,
        profile: str | None = None,
        initial_size: int | None = None,
        max_nodes: int | None = None,
        min_nodes: int | None = None,
        min_lat: float | None = None,
        max_lat: float | None = None,
        sample_interval: float | None = None,
        actor_domain: str | None = None,
        workload: WorkloadConfig | None = None,
    ) -> None:
        if config is None:
            workload = workload or WorkloadConfig()
            config = ScenarioConfig(
                profile=profile or ScenarioConfig.profile,
                actor_domain=actor_domain or ScenarioConfig.actor_domain,
                cluster=ClusterConfig(
                    initial_size=initial_size if initial_size is not None else ClusterConfig.initial_size,
                    max_nodes=max_nodes if max_nodes is not None else ClusterConfig.max_nodes,
                    min_nodes=min_nodes if min_nodes is not None else ClusterConfig.min_nodes,
                    replication_factor=workload.replication_factor,
                    sample_interval=sample_interval if sample_interval is not None else ClusterConfig.sample_interval,
                ),
                workload=workload,
                network=NetworkConfig(
                    min_lat=min_lat if min_lat is not None else NetworkConfig.min_lat,
                    max_lat=max_lat if max_lat is not None else NetworkConfig.max_lat,
                ),
            )
        self.env = env
        self.metrics = metrics
        self.clock_model = clock_model
        self.profile = CHURN_PROFILES[config.profile]
        self.actor_domain = config.actor_domain
        self.max_nodes = config.cluster.max_nodes
        self.min_nodes = config.cluster.min_nodes
        self.replication_factor = config.cluster.replication_factor
        self.network = config.network
        self.sample_interval = config.cluster.sample_interval
        self.workload = config.workload
        self.nodes: list[Node] = []
        self.clock_actor_slots = [f"r{index:04d}" for index in range(1, self.max_nodes + 1)]
        self.node_counter = 0
        self.version_counter = 0
        self.clients = [f"c{index:04d}" for index in range(1, self.workload.client_count + 1)]
        self.client_versions: dict[str, dict[str, list[VersionRecord]]] = defaultdict(
            lambda: defaultdict(list)
        )
        self.clock_actor_versions: dict[str, dict[str, list[VersionRecord]]] = defaultdict(
            lambda: defaultdict(list)
        )
        self._zipf_cdf = self._build_zipf_cdf()

        for _ in range(config.cluster.initial_size):
            self._add_node()

    def active_nodes(self) -> list[Node]:
        return [node for node in self.nodes if node.active]

    def active_node_ids(self) -> set[str]:
        return {node.id for node in self.active_nodes()}

    def active_slot_actor_ids(self) -> set[str]:
        return {node.state.actor_id for node in self.active_nodes()}

    def active_clock_actor_ids(self) -> set[str]:
        if self.actor_domain == "physical":
            return self.active_node_ids()
        if self.actor_domain == "slot":
            return self.active_slot_actor_ids()
        if self.actor_domain == "client":
            return set(self.clients)
        raise ValueError(f"Unknown actor domain: {self.actor_domain}")

    def clock_actor_for_write(self, *, client_id: str, coordinator: Node) -> str:
        if self.actor_domain == "physical":
            return coordinator.id
        if self.actor_domain == "slot":
            return coordinator.state.actor_id
        if self.actor_domain == "client":
            return client_id
        raise ValueError(f"Unknown actor domain: {self.actor_domain}")

    def active_count(self) -> int:
        return len(self.active_nodes())

    def choose_node(self) -> Node | None:
        active = self.active_nodes()
        return random.choice(active) if active else None

    def choose_key(self) -> str:
        if self.workload.key_count <= 1:
            return "k0"
        if self.workload.key_distribution == "zipf":
            return self._choose_key_zipf()
        if random.random() < self.workload.hot_key_probability:
            return "k0"
        return f"k{random.randint(1, self.workload.key_count - 1)}"

    def _build_zipf_cdf(self) -> list[float]:
        if self.workload.key_distribution != "zipf":
            return []
        if self.workload.key_count <= 0:
            return []
        if self.workload.zipf_skew <= 0.0:
            return []
        weights = [1.0 / (float(rank) ** self.workload.zipf_skew) for rank in range(1, self.workload.key_count + 1)]
        total = sum(weights)
        if total <= 0.0:
            return []
        cumulative = []
        current = 0.0
        for weight in weights:
            current += weight / total
            cumulative.append(current)
        cumulative[-1] = 1.0
        return cumulative

    def _choose_key_zipf(self) -> str:
        if self.workload.key_distribution != "zipf":
            raise RuntimeError("Zipf key distribution is disabled.")
        cdf = self._zipf_cdf
        if not cdf:
            return f"k{random.randint(0, self.workload.key_count - 1)}"
        rank = bisect.bisect_left(cdf, random.random())
        return f"k{min(rank, self.workload.key_count - 1)}"

    def choose_client(self) -> str:
        return random.choice(self.clients)

    def allocate_write_actor(self) -> str:
        """Choose the logical writer actor for exact client VV.

        Earlier versions allocated a fresh session actor for every write, which
        made the exact VV baseline grow almost one actor per event. Reusing the
        configured client pool gives a bounded, fairer vanilla-VV baseline for
        report comparisons. Replica-actor clocks ignore this value.
        """
        return self.choose_client()

    def context_for_client(
        self,
        actor_id: str,
        key: str,
        read_versions: list[VersionRecord],
    ) -> list[VersionRecord]:
        """Combine object-store reads with the client's carried session context."""
        by_id = {version.version_id: version for version in read_versions}
        for version in self.client_versions[actor_id][key]:
            by_id.setdefault(version.version_id, version)
        return list(by_id.values())

    def remember_client_version(self, actor_id: str, version: VersionRecord) -> None:
        self.client_versions[actor_id][version.key] = [version]

    def replication_targets(self, coordinator: Node) -> list[Node]:
        peers = [node for node in self.active_nodes() if node.id != coordinator.id]
        max_targets = max(self.replication_factor - 1, 0)
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
        update_active_actors = getattr(self.clock_model, "update_active_actors", None)
        if update_active_actors is not None:
            update_active_actors(self.active_clock_actor_ids(), self.env.now)
        clock_actor_id = self.clock_actor_for_write(client_id=actor_id, coordinator=coordinator)
        by_id = {version.version_id: version for version in context_versions}
        for version in self.clock_actor_versions[clock_actor_id][key]:
            by_id.setdefault(version.version_id, version)
        context_versions = list(by_id.values())
        read_context = self.clock_model.build_read_context(context_versions)
        stamp = self.clock_model.issue_stamp(
            coordinator.state,
            key,
            read_context,
            self.env.now,
            clock_actor_id,
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
        self.remember_client_version(actor_id, version)
        self.clock_actor_versions[clock_actor_id][version.key] = [version]
        self._record_accuracy(version)
        represented_context = version.stamp.represented_context()
        targets = self.replication_targets(coordinator)
        self.metrics.record_write(
            t=self.env.now,
            version=version,
            node_id=coordinator.id,
            actor_id=clock_actor_id,
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
            delay = random.uniform(self.network.min_lat, self.network.max_lat)
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
                client_id = self.allocate_write_actor()
                target_node = coordinator

                def commit() -> None:
                    write_node = target_node
                    if not write_node.active:
                        fallback = self.choose_node()
                        if fallback is None:
                            return
                        write_node = fallback
                    read_versions = self.context_for_client(client_id, key, write_node.read(key))
                    phase = (
                        "merge"
                        if len(read_versions) > 1
                        and random.random() < self.workload.merge_probability
                        else "background"
                    )
                    self.execute_write(
                        key=key,
                        coordinator=write_node,
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
                    client_id = self.allocate_write_actor()
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
                            context_versions=self.context_for_client(writer_id, key, shared_context),
                            phase="burst",
                            actor_id=writer_id,
                        )

                    self.env.schedule(random.uniform(0.0, self.workload.burst_spread), burst_write)

                def merge_write() -> None:
                    target = self.choose_node()
                    if target is None:
                        return
                    actor_id = self.allocate_write_actor()
                    self.execute_write(
                        key=key,
                        coordinator=target,
                        context_versions=self.context_for_client(actor_id, key, target.read(key)),
                        phase="burst_merge",
                        actor_id=actor_id,
                    )

                self.env.schedule(self.workload.merge_delay, merge_write)

            self.env.schedule(interval, burst)

        self.env.schedule(interval, burst)

    def _allocate_clock_actor_id(self) -> str:
        active_actors = self.active_slot_actor_ids()
        for actor_id in self.clock_actor_slots:
            if actor_id not in active_actors:
                return actor_id
        return f"r{len(self.clock_actor_slots) + 1:04d}"

    def _add_node(self) -> None:
        self.node_counter += 1
        node = Node(
            env=self.env,
            node_id=f"n{self.node_counter:04d}",
            clock_actor_id=self._allocate_clock_actor_id(),
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
        join_rate = self.profile.join_rate
        leave_rate = self.profile.leave_rate
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
        burst_size = self.profile.burst_size
        interval = self.profile.burst_interval
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
            active_clock_actors = self.active_clock_actor_ids()
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
                            replica_actor_set = {actor for actor in actor_set if not actor.startswith("c")}
                            stale_count = len(replica_actor_set - active_clock_actors)
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
    clock_factory: Callable[[], ClockModel],
    config: ScenarioConfig | None = None,
    progress: bool = False,
    progress_label: str | None = None,
    **legacy_kwargs: Any,
) -> MetricsCollector:
    """Run one scenario.

    New code should pass ``config``. ``legacy_kwargs`` keeps older tests and
    scripts working while centralizing the many scenario knobs in dataclasses.
    """

    if config is None:
        config = scenario_config_from_kwargs(**legacy_kwargs)
    random.seed(config.seed)
    env = Environment()
    metrics = MetricsCollector()
    cluster = Cluster(
        env=env,
        metrics=metrics,
        clock_model=clock_factory(),
        config=config,
    )
    cluster.start()
    env.run(until=config.sim_time, progress=progress, desc=progress_label)
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

