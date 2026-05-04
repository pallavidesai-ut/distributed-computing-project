# Simulator Methodology

This document summarizes how the current simulator works and what additional data would strengthen the final report.

## What the Simulator Models

The simulator is a discrete-event model of a partially replicated key-value store. It is designed to compare per-object causal metadata schemes under membership churn and hot-key contention.

The main goal is not to model every production cost. Instead, the simulator isolates one question:

> How much metadata is needed to preserve per-object causal ancestry, and what semantic errors appear when metadata is compressed or pruned?

## Core Entities

### Environment

`clocksim/sim.py` defines a minimal discrete-event engine. Events are scheduled on a heap queue and executed in timestamp order until the configured simulation time.

### Cluster

A `Cluster` manages:

- active and inactive replicas;
- join/leave churn events;
- background client writes;
- hot-key contention bursts;
- delayed partial replication;
- periodic state snapshots.

The supported churn profiles are:

- `stable`: no joins or leaves;
- `low`: slow continuous joins/leaves;
- `sustained`: higher continuous churn;
- `burst`: periodic removal and rejoin bursts plus low background churn.

### Nodes and Versions

Each node stores a per-key sibling set:

```text
key -> [VersionRecord, VersionRecord, ...]
```

A `VersionRecord` contains:

- object key;
- clock stamp;
- origin replica;
- creation time;
- workload phase;
- read set size;
- true causal history.

The true causal history is represented separately from the clock metadata. This is the most important methodological feature of the simulator.

## Workload

The workload combines three kinds of activity:

1. **Background writes**
   - Clients choose keys according to a hot-key distribution.
   - Each write reads local versions from a coordinator, combines them with the client's carried session context, and writes a new version.

2. **Contention bursts**
   - Multiple clients write concurrently to the hot key `k0` from a shared starting context.
   - This creates sibling pressure and tests whether clocks distinguish concurrency from ancestry.

3. **Merge writes**
   - After bursts, a later write may read multiple siblings and produce a descendant that should supersede them.
   - This tests whether clocks retain enough ancestry to drop stale siblings.

Replication is partial: each write is applied at the coordinator and sent to a configured number of active target replicas after a random network delay.

## Clock Models

### Exact client-actor Version Vector: `vv`

- Actor identity: bounded client/session actor.
- Metadata: vector from client actor to counter.
- Expected behavior: exact ancestry precision and recall.
- Expected cost: metadata grows with distinct writers touching an object.

This is the fair exact VV baseline.

### Exact Dotted Version Vector: `dvv`

- Actor identity: replica-issued dot.
- Metadata: compact context summary plus a dot for the current write.
- Expected behavior: exact ancestry precision and recall.
- Expected cost: lower than exact client-actor VV when client cardinality exceeds replica/context width.

This is the main exact optimization under study.

### Lease-pruned DVV: `lease_dvv`

- Starts from the DVV representation.
- Before issuing a new write, expired actors/events are pruned from the context.
- Leases are renewed when stamps are observed.
- Expected behavior: precision remains high because pruning forgets events rather than inventing them; recall may drop.
- Expected cost: shorter leases reduce metadata but increase stale sibling retention.

This is an approximate/tunable design, not a correctness-preserving replacement for exact DVV.

### Membership-aware lease DVV: `membership_lease_dvv`

- Starts from the DVV representation.
- Keeps all currently active replica actors, even if they have been quiet longer than the lease duration.
- Starts the lease countdown only when a replica actor leaves active membership.
- Expected behavior: stable profiles should behave close to exact DVV, while churn profiles can prune departed replica actors after the grace period.
- Expected cost: less metadata reduction than fixed lease-DVV in quiet/stable periods, but better recall and lower stale-sibling pressure.

This is a more production-style approximation than fixed lease-DVV because it targets departed actors rather than quiet actors.

### Coarse vnode Version Vector: `vv_vnode`

- Actor identity: replica/vnode actor.
- Metadata: vector over replica actors.
- Expected behavior: compact but semantically coarse under per-client causal ground truth.
- Failure mode: false ancestry/over-ordering, causing missed conflicts.

This should be presented as a production-style baseline or appendix result, not as the fair exact VV baseline.

## Ground Truth vs Clock-Encoded History

For every write, the simulator constructs two histories:

1. **True causal history**
   - Derived from read-then-write dependencies.
   - Includes the new event and all events in the versions read by the client/write.

2. **Represented clock history**
   - Derived from the selected clock stamp's represented context.

Comparing these histories yields:

- true positives;
- false positives;
- false negatives;
- precision;
- recall.

This makes it possible to distinguish metadata savings from semantic loss.

## Version Application and Conflict Decisions

When a node receives or creates a version, it compares the incoming version to each existing sibling for the same key.

For each pair, the simulator records:

- the true relation from ground-truth histories;
- the clock relation from clock metadata;
- the action taken by the store:
  - drop incoming;
  - drop existing;
  - keep both.

This supports two important error metrics:

- **Missed-conflict rate**: concurrent writes incorrectly collapsed by the clock.
- **Stale-sibling rate**: true descendants incorrectly kept alongside ancestors because the clock forgot ancestry.

## Metrics

Primary metrics:

- average metadata bytes;
- p95 metadata bytes;
- average actor entries;
- ancestry precision;
- ancestry recall;
- false positive events;
- false negative events;
- missed-conflict rate;
- stale-sibling rate;
- average hot-key siblings;
- stale replica-actor fraction;
- pruned write rate for lease-DVV.

Secondary sanity metrics:

- replication deliveries;
- replication latency;
- logical write throughput;
- joins and leaves.

Metadata bytes are JSON serialization bytes for the clock payload, excluding the metadata type label. They should be interpreted as relative, not optimized wire-format, costs.

## What the Current Final Run Shows

The latest final run supports the intended paper story:

- Exact `vv` and exact `dvv` both have precision and recall of 1.0.
- Exact `dvv` reduces average metadata versus exact `vv` by roughly 52--60% depending on profile.
- `lease_dvv_L32` is close to exact DVV with small recall loss.
- `lease_dvv_L16` gives moderate metadata savings and moderate recall/stale-sibling cost.
- `lease_dvv_L8` gives large metadata savings but severe recall loss and high stale-sibling pressure.
- `vv_vnode` is compact but has lower precision and nonzero missed-conflict rates, so it should not be framed as exact VV.

## What Additional Data Would Strengthen the Report

The current results are sufficient for a class project report if written carefully. For a stronger publication-style paper, add the following in priority order.

### 1. Confidence intervals or error bars

The current plots show seed averages but not variability. Add standard deviation, standard error, or 95% confidence intervals to key aggregate plots:

- metadata bytes by profile;
- recall by profile;
- stale sibling rate by profile;
- missed conflict rate by profile.

This is the most important missing reporting feature.

### 2. Replication-factor sensitivity

Run the included smaller sensitivity sweep for replication factors 3 and 5, comparing against the main RF=4 run.

```bash
scripts/reproduce_sensitivity.sh
```

Or run one configuration directly:

```bash
scripts/reproduce_final.sh configs/sensitivity_rf3.yaml
scripts/reproduce_final.sh configs/sensitivity_rf5.yaml
```

Why it matters:

- DVV uses replica-issued dots, so replica count and replication fanout can affect context width.
- Higher replication may reduce missing context but increase metadata/state pressure.
- Lower replication may increase divergence and sibling pressure.

### 3. Client-count sensitivity

Run the included client-count sensitivity sweep for 32 and 512 clients, comparing against the main 128-client run.

```bash
scripts/reproduce_sensitivity.sh
```

Or run one configuration directly:

```bash
scripts/reproduce_final.sh configs/sensitivity_client_count_32.yaml
scripts/reproduce_final.sh configs/sensitivity_client_count_512.yaml
```

Why it matters:

- Exact VV should become more expensive as distinct client actor cardinality increases.
- DVV should be less sensitive because it uses replica-issued dots.
- This directly strengthens the claim that DVV saves metadata relative to client-actor VV.

### 4. Extreme stress scenarios

Two stress configurations are included to make differences easier to see:

```bash
scripts/reproduce_extremes.sh
```

- `configs/extreme_hotspot_churn.yaml`: high hot-key probability, more clients, more burst writers, and sustained/burst churn. This amplifies metadata growth, lease pruning, and stale-sibling pressure.
- `configs/extreme_sparse_replication.yaml`: low replication factor, many clients, higher latency, and lower merge probability. This amplifies replica divergence and conflict-resolution differences.

These scenarios should be treated as stress tests, not the headline balanced workload.

### 5. Lease-duration normalization

Current leases are fixed at 8, 16, and 32 simulation-time units. Add a small explanation or sweep relating lease duration to:

- network latency range;
- client think time;
- churn rate;
- burst interval.

A stronger report would say why L8/L16/L32 are meaningful operating points.

### 5. Deterministic DVV advantage example

Add a small explanatory figure or table, not necessarily a full simulation:

- two independent clients write through the same replica;
- `vv_vnode` falsely orders them;
- `dvv` keeps them concurrent because each write has a distinct dot.

This would make DVV's unique advantage obvious before the aggregate results.

### 6. Optional approximate-clock comparator

If time allows, add a Bloom-clock-style approximate baseline.

Why it would help:

- Bloom-style clocks usually fail by false positives/over-ordering.
- Lease-DVV fails by false negatives/forgotten ancestry.
- Comparing them would make the approximate-clock design space clearer.

This is optional and should not block the current report.

## Recommended Report Framing

Use this language:

> Exact VV is correct but metadata-heavy. Exact DVV preserves the same per-object ancestry correctness with lower metadata in this workload. Lease-DVV is an approximate design that exposes a metadata-versus-recall knob. Coarse vnode VV is compact but can create false ancestry and missed conflicts.

Avoid this language:

> Vanilla VV is lossy.

That is not true for the fair client-actor VV baseline.
