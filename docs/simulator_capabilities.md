# Simulator Capabilities

This document summarizes what the simulator supports, how it is configured, and the main behaviors it is designed to measure.

## Core Purpose

The simulator is a discrete-event study harness for **per-object causal metadata** in a partially replicated key-value style system. It compares exact and approximate causal-clock variants under contention, churn, and different actor-domain choices to quantify:

- metadata overhead,
- ancestry precision and recall against ground truth,
- and operational side effects such as stale siblings or missed conflicts.

## Supported Clock Models

- `vv`  
  Exact client-actor version vector.
- `dvv`  
  Exact dotted version vector.
- `itc`  
  Interval Tree Clock (when enabled in the study).
- `lease_dvv`  
  Approximate DVV with fixed lease-based context pruning.
- `membership_lease_dvv`  
  Approximate DVV with membership-aware lease pruning (keeps active actors longer, prunes departed actors).
- `vv_vnode` (configured as a coarse baseline)  
  Exact-by-actor for replica/vnode identity, used as a semantic-comparison baseline.

## Actor-Domain Modes

- `physical` (default): churn-created replica identities (`n0001`, `n0002`, …)
- `slot`: logical replica/vnode slots (`r0001`, `r0002`, …)
- `client`: session/client actors (`c0001`, `c0002`, …)

Clock metadata is per object/version; actor-domain choice changes only the meaning of identity mapping, not the underlying event model.

## Simulation Model

- Discrete-event scheduler with event heap (`clocksim/sim.py`)
- Cluster model with:
  - joins/leaves and membership churn,
  - background writes,
  - optional contention bursts and merge events,
  - configurable replication fanout and delays,
  - per-key sibling tracking.
- Per-key, per-version state includes: object key, clock stamp, creator, timestamp, workload context, and true read-write history.
- Workload distributions include configurable hot-key and Zipfian access patterns.

## Ground-Truth Methodology

For each write, the simulator records:

1. **True causal history** (from read dependencies),
2. **Represented clock history** (from the selected clock stamp).

Comparing these histories yields per-event precision/recall and sibling decision effects.

## Metrics and Outputs

Primary analysis covers:

- metadata size (bytes, JSON-serialized),
- average actor-count entries,
- ancestry precision and recall,
- missed-conflict rate,
- stale-sibling rate,
- stale replica-actor fraction,
- replication throughput/latency,
- churn/join/leave counters.

Results are produced by:

- `analyze_run.py` (per-run summaries and plots),
- `run_experiments.py` (matrix execution and aggregate outputs),
- `scripts/reproduce_final.sh` (timestamped reproducibility workflow).

## Exact vs Approximate Semantics

- Exact `vv`, `dvv`, and `itc` are expected to preserve ancestry correctly on the same actor domain.
- `lease_dvv` and membership variants are intentionally approximate and trade metadata reduction for recall/staleness behavior.
- Approximate loss here is from pruning / coarse actor mapping, not from “vanilla” VV itself.
