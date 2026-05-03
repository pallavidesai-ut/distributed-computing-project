# Code Overview

## What the code does

This project is a discrete-event simulator for a partially replicated key-value store. It uses nodes, replication delay, churn, and client writes to generate realistic execution traces, but the main semantic object of study is **per-object causal history**.

The simulator compares causal metadata schemes for object versions:

- `vv` — exact Version Vectors over bounded client actors
- `vv_vnode` — production-style Version Vectors over replica/vnode actors
- `dvv` — exact Dotted Version Vectors
- `lease_dvv` — Dotted Version Vectors with lease-based write-time pruning

Every version tracks both:

1. **Ground-truth object history** using object-scoped `EventId(key, actor, counter)` values.
2. **Clock-encoded history** using the selected clock metadata.

This lets the analysis measure metadata cost and semantic fidelity separately.

---

## Package layout

The simulator implementation lives in the `clocksim/` package:

```text
clocksim/
  __init__.py   # public compatibility exports
  context.py    # Dot, EventId, CausalContext, context comparison helpers
  clocks.py     # VV/DVV/lease-DVV stamps and clock models
  store.py      # VersionRecord, Message, Node apply/merge behavior
  metrics.py    # MetricsCollector and summary helpers
  sim.py        # Environment, WorkloadConfig, Cluster, run_scenario, save_run
  cli.py        # argument parser and main()

code.py         # backwards-compatible script wrapper
run_experiments.py
analyze_run.py
```

Public APIs are re-exported from `clocksim.__init__` and remain available through `code.py`:

- `run_scenario`
- `save_run`
- `make_clock_factory`
- `CLOCK_FACTORIES`
- `CHURN_PROFILES`
- `main`

Main commands:

```bash
python code.py --clock lease_dvv --profile sustained
python run_experiments.py
python analyze_run.py --input-dir output/runs --run-name some_run
```

---

## Components

### 1. Entry point (`code.py`, `clocksim/cli.py`)

`code.py` is a compatibility wrapper around the `clocksim` package. It re-exports the main simulator APIs and calls `main()` when run directly.

`clocksim/cli.py` contains parser setup and the CLI `main()` implementation.

---

### 2. Simulation engine (`clocksim/sim.py`)

`Environment` is a lightweight discrete-event scheduler built on a heap queue.

- `env.schedule(delay, callback)` schedules future work.
- `env.run(until=T)` processes callbacks up to simulation time `T`.

`Cluster` uses the environment to drive background writes, churn, contention bursts, replication delivery, and snapshots.

---

### 3. Causal context model (`clocksim/context.py`)

Core causal data structures:

- `Dot(actor, counter)` — clock-level event identifier.
- `EventId(key, actor, counter)` — object-scoped ground-truth event identity.
- `CausalContext(prefix, dots)` — prefix summary plus explicit exceptions/dots.

Important helper functions:

- `compact_context`
- `union_contexts`
- `context_includes`
- `compare_contexts`

The `EventId` distinction matters because replica clocks use per-object counters. Without the key, `n1:1` on `k0` and `n1:1` on `k1` would be confused as the same true event.

---

### 4. Clock stamps (`clocksim/clocks.py`)

Clock metadata is represented by stamp objects:

- `VVStamp`
- `DVVStamp`

Each stamp can:

- return its represented causal context
- serialize itself
- report metadata component count
- report serialized metadata bytes
- report pruning information for lease-DVV

---

### 5. Clock models (`clocksim/clocks.py`)

Clock implementations are exposed through `ClockModel`:

- `VersionVectorModel`
  - exact per-object vector over bounded client actors
- `VnodeVersionVectorModel`
  - production-style vector over replica actors
  - can collapse distinct client writes through the same coordinator
- `DottedVersionVectorModel`
  - exact dotted version vector over replica actors
- `LeaseDottedVersionVectorModel`
  - DVV with lease-based pruning during new stamp creation

Clock models implement:

- `build_read_context(versions)`
- `issue_stamp(...)`
- `observe_stamp(...)`
- `compare_stamps(left, right)`

---

### 6. Node / object store (`clocksim/store.py`)

`Node` represents a replica. Each node stores per-key sibling sets:

```python
kv: dict[str, list[VersionRecord]]
```

Important behavior:

- `read(key)` returns current sibling versions for that object.
- `apply_version(version)` compares the incoming version against existing siblings.
- dominated versions are dropped.
- concurrent versions are kept as siblings.

This is where clock comparison affects conflict behavior.

---

### 7. Cluster and workload (`clocksim/sim.py`)

`Cluster` manages active nodes, churn, clients, writes, replication, and snapshots.

Churn profiles:

| Profile | Behavior |
| --- | --- |
| `stable` | No joins or leaves after startup |
| `low` | Slow trickle of joins/leaves |
| `sustained` | Continuous moderate churn |
| `burst` | Periodic mass departure followed by rejoins |

Workload features:

- partial replication
- configurable replication factor
- hot-key probability
- bounded client actor pool
- per-key client session context so repeated client writes remain causal for exact VV
- background writes
- explicit hot-key contention bursts
- delayed merge writes

---

### 8. Metrics (`clocksim/metrics.py`)

The simulator records:

- writes
- replication deliveries
- conflict decisions
- snapshots
- joins/leaves
- ancestry accuracy rows

Important summary metrics:

- `avg_metadata_bytes`
- `p95_metadata_bytes`
- `avg_actor_entries`
- `avg_history_precision`
- `avg_history_recall`
- `missed_conflict_rate`
- `stale_sibling_rate`
- `avg_hot_key_siblings`
- `avg_stale_actor_fraction`
- `avg_latency`
- `pruned_write_rate`

---

## Lease-DVV pruning / garbage collection

There is currently **no separate background garbage collector**.

Lease-DVV pruning happens when a new stamp is issued:

```python
LeaseDottedVersionVectorModel.issue_stamp(...)
```

Observed actors renew leases through `observe_stamp`. When a new lease-DVV write is created, expired actors/events are omitted from the new metadata. Existing stored versions are not rewritten by a background compactor.

So the current implementation should be described as:

> lease-based write-time metadata pruning

not full storage garbage collection.

The current metrics are therefore strongest for measuring:

- metadata attached to new writes
- ancestry recall/precision loss from pruning
- sibling behavior caused by approximate history

They do **not** model CPU cost, storage compaction cost, or realistic background GC overhead.

---

## Testing

The pytest suite covers the key per-object semantics:

- causal context algebra
- stamp represented histories
- object-scoped event identity
- same-object read/write dominance
- concurrent same-key sibling behavior
- merge write dominance
- VV/DVV exact history fidelity
- vnode-VV same-coordinator collapse
- lease-DVV long-lease vs expired-lease behavior
- small scenario smoke tests

Run tests with:

```bash
uv run pytest -q
```

---

## Current status

The simulator is functional, packaged as `clocksim/`, and produces report-oriented experiment outputs under:

```text
output/experiments/per_object_clock_study/
```

The latest full fair-VV validation run was written to:

```text
output/experiments/per_object_clock_study_fair_vv/
```
