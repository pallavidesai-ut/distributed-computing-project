# Simulator Evolution Log

## Stage 1: Exploratory Clock Code

The original project code was exploratory and flattened much of causality into frontier-like maps. That was useful for learning the APIs, but it was not enough for a research-ready comparison because it blurred the semantic differences between VV, vnode-VV, DVV, and approximate pruning.

## Stage 2: Per-Object Causal Histories

The simulator now maintains two histories per version:

- ground-truth history from simulated read dependencies
- clock-encoded history from the selected clock implementation

This lets analysis measure ancestry precision, ancestry recall, false positive events, and false negative events. Exact clocks can now be distinguished from approximate clocks in the output rather than only by metadata shape.

## Stage 3: Churn-Aware Replicated KV Model

The workload now includes:

- active replica membership with joins and leaves
- partial replication with configurable replication factor
- delayed message delivery
- per-key sibling sets
- background read-then-write traffic
- hot-key contention bursts
- delayed merge writes after bursts

Snapshots record active node count, sibling pressure, stored metadata size, actor-entry counts, and stale replica-actor fraction.

## Stage 4: Clock Families

The implemented clock families are:

- `vv`: exact per-object version vectors over client/session actors
- `vv_vnode`: production-style version vectors over replica actors
- `dvv`: dotted version vectors over replica actors
- `lease_dvv`: DVV with actor-expiry pruning before new writes

The important semantic split is that `vv_vnode` can falsely order independent writes coordinated by the same replica, while DVV can represent those writes as distinct dots.

## Stage 5: Analysis and Report Pipeline

Each run writes raw CSVs for writes, deliveries, decisions, snapshots, joins, leaves, and history accuracy. `analyze_run.py` turns a single run into:

- metadata-over-time tables and plots
- history-fidelity tables and plots
- conflict-decision quality tables and plots
- replica-state pressure tables and plots
- latency summaries

`run_experiments.py` runs a matrix across profiles, clocks, seeds, and lease durations. It produces aggregate comparison tables, report figures, lease ablation tables, and a generated `study_report.md`.

## Stage 6: Lease-Duration Ablations

Lease duration is now a first-class experiment dimension. Non-lease clocks run once per profile/seed. `lease_dvv` runs once per configured duration and is labeled as variants such as `lease_dvv_L8`, `lease_dvv_L16`, and `lease_dvv_L32`.

The ablation outputs are:

- `lease_duration_ablation.csv`
- `lease_ablation_metadata.png`
- `lease_ablation_recall.png`
- `lease_ablation_stale_siblings.png`
- `lease_ablation_pruning.png`

These outputs are intended to support the core tradeoff claim: shorter leases reduce metadata more aggressively, but they should also increase ancestry loss and stale sibling retention.
