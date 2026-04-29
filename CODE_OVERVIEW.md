# Code Overview

## What the code does

`code.py` is a discrete-event simulation of a distributed key-value store. It models many nodes sending messages to each other, tracks causal ordering metadata (vector clocks), and measures how that metadata grows under different levels of membership churn.

---

## Components

### 1. Simulation Engine (`Environment`)
A lightweight event scheduler built on a heap queue. You schedule callbacks at future times with `env.schedule(delay, fn)`, then call `env.run(until=T)` to process all events up to time T. This replaces SimPy for now.

### 2. Clock Interface (`BaseClock`)
An abstract base class that all clock implementations must follow. Any clock needs to:
- `local_event` — increment on a local write
- `prepare_send` — build the metadata payload to attach to an outgoing message
- `update_on_receive` — check if an incoming message can be delivered causally, and update local state if so
- `metadata_size` — report how many entries are in the clock (used for measurements)

Two implementations exist:
- **`NullClock`** — does nothing; baseline with zero metadata and no ordering
- **`VectorClock`** — standard vector clock; each node tracks a counter per known node. A message is only delivered when all causal dependencies are satisfied

### 3. Node
Each node periodically writes a random key/value and broadcasts it to all active peers. On receive, it checks the vector clock delivery condition. If the message isn't causally ready yet, it goes into a buffer and is retried whenever a later message is successfully delivered.

### 4. Cluster
Manages the set of active nodes and drives membership churn according to a named profile. Four profiles are defined:

| Profile | Behavior |
|---------|----------|
| `stable` | No joins or leaves |
| `low` | Slow trickle of joins/leaves |
| `sustained` | Continuous moderate churn |
| `burst` | Periodic mass departure followed by rejoins |

### 5. Metrics (`MetricsCollector`)
Records every send, delivery, buffer event, join, and leave. Key summary stats:
- `avg_metadata_size` — average number of entries in the clock per message sent
- `avg_latency` — average time from send to delivery
- `total_buffered` — messages that couldn't be delivered immediately
- `causal_violations` — (relevant for future lease-based clock) times causal order was broken

Results can be written to CSV files for analysis.

---

## What's missing

The simulation harness is complete. The two clock implementations that are the actual subject of the experiment have not been written yet:

- **`DVVClock`** — Dotted Version Vectors, which separate individual write events from the summary context to avoid the bloat that standard vector clocks accumulate under churn
- **`LeaseBasedDVV`** — extends DVV with expiring entries so that departed nodes are pruned from the metadata after a configurable time window

Once those are added, the plan is to run all three clocks (VC, DVV, Lease-DVV) across all four churn profiles and compare the metrics.
