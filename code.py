"""
sim.py  –  Causal-consistency under membership churn simulator
Pure-Python discrete-event engine (heapq); no SimPy dependency required.
Drop-in swap for SimPy once that env is available.
"""

import heapq, random, csv, copy, json
from abc import ABC, abstractmethod
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Optional, Callable

# ─────────────────────────────────────────────────────────────
# 1.  DISCRETE EVENT ENGINE
# ─────────────────────────────────────────────────────────────


class Environment:
    """Minimal discrete-event simulation core (heapq-based)."""

    def __init__(self):
        self._queue = []  # (time, seq, callback)
        self._seq = 0
        self.now = 0.0

    def schedule(self, delay: float, callback: Callable) -> None:
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


# ─────────────────────────────────────────────────────────────
# 2.  CLOCK INTERFACE  +  IMPLEMENTATIONS
# ─────────────────────────────────────────────────────────────


class BaseClock(ABC):
    @abstractmethod
    def local_event(self, node_id: str):
        pass

    @abstractmethod
    def prepare_send(self, node_id: str) -> dict:
        pass

    @abstractmethod
    def update_on_receive(self, node_id: str, metadata: dict) -> bool:
        pass

    @abstractmethod
    def metadata_size(self) -> int:
        pass

    @abstractmethod
    def clone(self) -> "BaseClock":
        pass


class NullClock(BaseClock):
    """No-op placeholder for harness testing."""

    def local_event(self, node_id):
        pass

    def prepare_send(self, node_id):
        return {}

    def update_on_receive(self, node_id, metadata):
        return True

    def metadata_size(self):
        return 0

    def clone(self):
        return NullClock()


class VectorClock(BaseClock):
    """Standard O(n) vector clock with causal delivery check."""

    def __init__(self, initial: dict = None):
        self.vc: dict[str, int] = defaultdict(int)
        if initial:
            self.vc.update(initial)

    def local_event(self, node_id: str):
        self.vc[node_id] += 1

    def prepare_send(self, node_id: str) -> dict:
        self.local_event(node_id)
        return dict(self.vc)

    def update_on_receive(self, node_id: str, metadata: dict) -> bool:
        """
        Causal delivery rule:
          sender entry  must equal  local[sender] + 1
          all other entries must be <= local[k]
        If not deliverable, return False (caller should buffer).
        """
        sender = metadata.get("__sender__")
        for k, v in metadata.items():
            if k == "__sender__":
                continue
            local_v = self.vc.get(k, 0)
            if k == sender:
                if v != local_v + 1:
                    return False
            else:
                if v > local_v:
                    return False
        for k, v in metadata.items():
            if k != "__sender__":
                self.vc[k] = max(self.vc.get(k, 0), v)
        return True

    def metadata_size(self) -> int:
        return len(self.vc)

    def clone(self) -> "VectorClock":
        return VectorClock(dict(self.vc))


# ─────────────────────────────────────────────────────────────
# 3.  METRICS
# ─────────────────────────────────────────────────────────────


class MetricsCollector:
    def __init__(self):
        self.sends = []
        self.deliveries = []
        self.joins = []
        self.leaves = []
        self.buffered = []
        self.violations = []

    def record_send(self, node_id, meta_size, t):
        self.sends.append({"t": round(t, 2), "node": node_id, "meta_size": meta_size})

    def record_delivery(self, sender, receiver, latency, meta_size, t):
        self.deliveries.append(
            {
                "t": round(t, 2),
                "sender": sender,
                "receiver": receiver,
                "latency": round(latency, 2),
                "meta_size": meta_size,
            }
        )

    def record_buffered(self, node_id, t):
        self.buffered.append({"t": round(t, 2), "node": node_id})

    def record_violation(self, node_id, t):
        self.violations.append({"t": round(t, 2), "node": node_id})

    def record_join(self, nid, t, sz):
        self.joins.append({"t": round(t, 2), "node": nid, "cluster_size": sz})

    def record_leave(self, nid, t, sz):
        self.leaves.append({"t": round(t, 2), "node": nid, "cluster_size": sz})

    def save(self, prefix="results"):
        for name, rows in [
            ("sends", self.sends),
            ("deliveries", self.deliveries),
            ("joins", self.joins),
            ("leaves", self.leaves),
            ("buffered", self.buffered),
            ("violations", self.violations),
        ]:
            if not rows:
                continue
            fname = f"{prefix}_{name}.csv"
            with open(fname, "w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=rows[0].keys())
                w.writeheader()
                w.writerows(rows)
            print(f"  Wrote {len(rows):>5} rows → {fname}")

    def summary(self) -> dict:
        avg_meta = (
            sum(r["meta_size"] for r in self.sends) / len(self.sends)
            if self.sends
            else 0
        )
        avg_lat = (
            sum(r["latency"] for r in self.deliveries) / len(self.deliveries)
            if self.deliveries
            else 0
        )
        return {
            "total_sends": len(self.sends),
            "total_deliveries": len(self.deliveries),
            "total_buffered": len(self.buffered),
            "causal_violations": len(self.violations),
            "joins": len(self.joins),
            "leaves": len(self.leaves),
            "avg_metadata_size": round(avg_meta, 3),
            "avg_latency": round(avg_lat, 3),
        }


# ─────────────────────────────────────────────────────────────
# 4.  MESSAGE
# ─────────────────────────────────────────────────────────────

_MSG_ID = 0


def _next_msg_id():
    global _MSG_ID
    _MSG_ID += 1
    return _MSG_ID


@dataclass
class Message:
    sender_id: str
    receiver_id: str
    key: str
    value: Any
    metadata: dict
    sent_at: float
    msg_id: int = field(default_factory=_next_msg_id)


# ─────────────────────────────────────────────────────────────
# 5.  NODE
# ─────────────────────────────────────────────────────────────


class Node:
    def __init__(
        self,
        env: Environment,
        node_id: str,
        cluster: "Cluster",
        clock_factory: Callable,
        metrics: MetricsCollector,
        write_interval: float = 20.0,
        min_lat: float = 1.0,
        max_lat: float = 5.0,
    ):
        self.env = env
        self.id = node_id
        self.cluster = cluster
        self.clock = clock_factory()
        self.metrics = metrics
        self.kv: dict = {}
        self.buffer: list = []
        self.active = True
        self.write_interval = write_interval
        self.min_lat = min_lat
        self.max_lat = max_lat

    def start(self):
        self._schedule_write()

    def stop(self):
        self.active = False

    def _schedule_write(self):
        delay = random.expovariate(1.0 / self.write_interval)
        self.env.schedule(delay, self._do_write)

    def _do_write(self):
        if not self.active:
            return
        key = f"k{random.randint(0, 4)}"
        value = random.randint(0, 99)
        meta = self.clock.prepare_send(self.id)
        meta["__sender__"] = self.id
        self.kv[key] = value
        self.metrics.record_send(self.id, self.clock.metadata_size(), self.env.now)

        for peer in self.cluster.active_peers(self.id):
            msg = Message(
                self.id, peer.id, key, value, copy.deepcopy(meta), self.env.now
            )
            delay = random.uniform(self.min_lat, self.max_lat)

            # Capture peer reference at schedule time
            def make_deliver(node, message):
                def deliver():
                    if node.active:
                        node._receive(message)
                        self.metrics.record_delivery(
                            message.sender_id,
                            node.id,
                            self.env.now - message.sent_at,
                            len(message.metadata),
                            self.env.now,
                        )

                return deliver

            self.env.schedule(delay, make_deliver(peer, msg))

        self._schedule_write()

    def _receive(self, msg: Message):
        if self.clock.update_on_receive(self.id, msg.metadata):
            self.kv[msg.key] = msg.value
            self._retry_buffer()
        else:
            self.buffer.append(msg)
            self.metrics.record_buffered(self.id, self.env.now)

    def _retry_buffer(self):
        changed = True
        while changed:
            changed = False
            still = []
            for msg in self.buffer:
                if self.clock.update_on_receive(self.id, msg.metadata):
                    self.kv[msg.key] = msg.value
                    changed = True
                else:
                    still.append(msg)
            self.buffer = still


# ─────────────────────────────────────────────────────────────
# 6.  CLUSTER  (membership + churn)
# ─────────────────────────────────────────────────────────────

CHURN_PROFILES = {
    "stable": {
        "join_rate": 0.000,
        "leave_rate": 0.000,
        "burst_size": 0,
        "burst_interval": None,
    },
    "low": {
        "join_rate": 0.010,
        "leave_rate": 0.010,
        "burst_size": 0,
        "burst_interval": None,
    },
    "sustained": {
        "join_rate": 0.030,
        "leave_rate": 0.030,
        "burst_size": 0,
        "burst_interval": None,
    },
    "burst": {
        "join_rate": 0.005,
        "leave_rate": 0.005,
        "burst_size": 5,
        "burst_interval": 60.0,
    },
}


class Cluster:
    def __init__(
        self,
        env: Environment,
        metrics: MetricsCollector,
        initial_size: int,
        clock_factory: Callable,
        profile: str = "stable",
        max_nodes: int = 50,
        min_nodes: int = 5,
        write_interval: float = 20.0,
        min_lat: float = 1.0,
        max_lat: float = 5.0,
    ):
        self.env = env
        self.metrics = metrics
        self.clock_factory = clock_factory
        self.max_nodes = max_nodes
        self.min_nodes = min_nodes
        self.write_interval = write_interval
        self.min_lat = min_lat
        self.max_lat = max_lat
        self._nodes: list[Node] = []
        self._counter = 0
        self._profile = CHURN_PROFILES[profile]

        for _ in range(initial_size):
            self._add_node()

    def active_peers(self, exclude_id: str) -> list:
        return [n for n in self._nodes if n.id != exclude_id and n.active]

    def active_count(self) -> int:
        return sum(1 for n in self._nodes if n.active)

    def _add_node(self):
        self._counter += 1
        nid = f"n{self._counter:04d}"
        node = Node(
            self.env,
            nid,
            self,
            self.clock_factory,
            self.metrics,
            self.write_interval,
            self.min_lat,
            self.max_lat,
        )
        self._nodes.append(node)
        node.start()
        self.metrics.record_join(nid, self.env.now, self.active_count())

    def _remove_node(self):
        active = [n for n in self._nodes if n.active]
        if len(active) <= self.min_nodes:
            return
        victim = random.choice(active)
        victim.stop()
        self.metrics.record_leave(victim.id, self.env.now, self.active_count())

    def start_churn(self):
        jr = self._profile["join_rate"]
        lr = self._profile["leave_rate"]
        bs = self._profile["burst_size"]
        bi = self._profile["burst_interval"]
        if jr + lr > 0:
            self._schedule_churn_event(jr, lr)
        if bs > 0:
            self.env.schedule(bi, lambda: self._burst_event(bs, bi))

    def _schedule_churn_event(self, jr, lr):
        rate = jr + lr
        delay = random.expovariate(rate)

        def do_churn():
            if random.random() < jr / rate:
                if self.active_count() < self.max_nodes:
                    self._add_node()
            else:
                self._remove_node()
            self._schedule_churn_event(jr, lr)

        self.env.schedule(delay, do_churn)

    def _burst_event(self, bs, bi):
        for _ in range(bs):
            self._remove_node()

        def rejoin():
            for _ in range(bs):
                if self.active_count() < self.max_nodes:
                    self._add_node()
            self.env.schedule(bi, lambda: self._burst_event(bs, bi))

        self.env.schedule(bi / 2, rejoin)


# ─────────────────────────────────────────────────────────────
# 7.  RUN SCENARIO
# ─────────────────────────────────────────────────────────────


def run_scenario(
    profile: str = "sustained",
    clock_factory=VectorClock,
    sim_time: float = 300.0,
    seed: int = 42,
    initial_size: int = 15,
    write_interval: float = 20.0,
    max_nodes: int = 40,
    min_nodes: int = 5,
    min_lat: float = 1.0,
    max_lat: float = 5.0,
) -> MetricsCollector:
    global _MSG_ID
    _MSG_ID = 0
    random.seed(seed)
    env = Environment()
    metrics = MetricsCollector()
    cluster = Cluster(
        env,
        metrics,
        initial_size,
        clock_factory,
        profile,
        max_nodes,
        min_nodes,
        write_interval,
        min_lat,
        max_lat,
    )
    cluster.start_churn()
    env.run(until=sim_time)
    return metrics


# ─────────────────────────────────────────────────────────────
# 8.  QUICK SMOKE TESTS
# ─────────────────────────────────────────────────────────────

print("=== NullClock stable (smoke test) ===")
m = run_scenario("stable", NullClock, sim_time=100, initial_size=5)
print(json.dumps(m.summary(), indent=2))

print("\n=== VectorClock stable ===")
m = run_scenario("stable", VectorClock, sim_time=100, initial_size=5)
print(json.dumps(m.summary(), indent=2))

print("\n=== VectorClock sustained churn ===")
m = run_scenario("sustained", VectorClock, sim_time=300, initial_size=15)
m.save("vc_sustained")
print(json.dumps(m.summary(), indent=2))
