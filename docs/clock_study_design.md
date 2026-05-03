# Per-Object Clock Study Design

## Why The Simulator Was Rebuilt

The previous simulator attached causality to nodes and then flattened all version metadata to a frontier-like map. That erased the main semantic difference between Version Vectors (VV) and Dotted Version Vectors (DVV), so the analysis could only show shape differences in metadata, not meaningful differences in conflict handling.

The rebuilt simulator models a replicated key-value store with:

- per-object sibling sets
- explicit read-then-write clients
- hot-key contention bursts
- later merge writes
- delayed replication under churn

It also keeps two histories for every version:

1. Ground-truth causal history from the simulated read dependencies
2. Clock-encoded history from VV, DVV, or lease-DVV

That separation is what makes the comparison defensible.

## Clock Map

| Clock | Representation | Best use | Expected weakness |
| --- | --- | --- | --- |
| VV | Exact per-object vector over client/session actors | Exact ancestry with simple semantics | Metadata grows with the number of distinct writers touching an object |
| Vnode-VV | Server/vnode version vector over replica actors | Production-similar baseline used in real KV stores before DVV | Proxy actors can blur client causality and trigger sibling pathologies |
| DVV | Prefix summary plus explicit dots over replica actors | Exact object-level ancestry with metadata bounded by replication degree | More complex representation and merge logic |
| Lease-DVV | DVV plus actor expiry before new writes | Churn-heavy settings where stale actor metadata dominates cost | Pruning can remove true ancestry and retain stale siblings |

## Related Alternatives Worth Discussing

- Interval Tree Clocks: designed for dynamic membership and decentralized actor creation, so they are a strong conceptual comparator for churn.
- Bounded Version Vectors: keep vector-style causality but explicitly address bounded representations.
- HLC and other timestamp hybrids: useful if the study broadens from exact version ancestry to approximate causal ordering.

These are relevant literature comparators, but the implemented end-to-end study stays focused on the three main clocks above so the paper can make one clear argument.

## Benchmarks Used In The New Study

Every run uses the same benchmark structure:

- stable, low, sustained, and burst churn profiles
- partial replication with a fixed replication factor
- a large client/session actor pool so VV pays its theoretical metadata cost honestly
- a mixed workload of background writes plus explicit hot-key contention bursts
- merge writes after bursts so sibling resolution is exercised repeatedly
- random seeds for repeated runs
- lease-duration sweeps for `lease_dvv` so the pruning/correctness tradeoff is visible rather than hidden behind one chosen parameter

The study now has two comparison tracks:

- Fairness track: `VV` vs `DVV` vs `lease-DVV`
- Production track: `Vnode-VV` vs `DVV` vs `lease-DVV`

## Metrics That Matter For The Paper

- Metadata cost: bytes and actor entries per write
- Ancestry fidelity: precision and recall of the encoded history against the true causal DAG
- Conflict handling: missed-conflict rate and stale-sibling rate
- Replica pressure: hot-key sibling count and stale-actor fraction over time
- Replication latency: included as a sanity metric, not the primary claim

## Expected Interpretation

- VV should remain exact, but its metadata should grow with writer cardinality on hot objects.
- Vnode-VV should look more production-like, but it may show precision loss or sibling explosion because server actors proxy multiple clients.
- DVV should match VV on precision and recall while using metadata closer to replication degree than client population.
- Lease-DVV should reduce metadata most under sustained and burst churn, with a measurable recall or stale-sibling penalty that defines its tradeoff region.

## Report-Ready Output Shape

`run_experiments.py` writes the top-level comparison artifacts:

- `comparison_runs.csv`: every profile/clock/seed run with flattened metrics
- `comparison_by_clock.csv`: seed-aggregated metrics by profile and clock variant
- `lease_duration_ablation.csv`: metadata, recall, stale-sibling, and pruning metrics by lease duration
- `study_report.md`: generated draft report with current aggregate results
- `*_vs_profile.png`: profile-level comparison figures
- `lease_ablation_*.png`: lease sensitivity figures
- `time_series_report/`: time-series figures and CSVs for report sections
