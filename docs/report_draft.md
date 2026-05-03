# Report Draft

## Working Title

Lease-Pruned Dotted Version Vectors Under Replica Churn

## Draft Thesis

High replica churn causes conventional per-object causal metadata to accumulate stale actor entries. Dotted Version Vectors preserve exact ancestry with lower ambiguity than production-style vnode Version Vectors, and a lease-pruned DVV can further reduce stale metadata by intentionally forgetting expired actor history. The useful contribution is not that lease-DVV is always correct; it is that the lease duration exposes a tunable metadata-versus-recall operating region.

## Methods Snapshot

The simulator models a partially replicated key-value store under stable, low, sustained, and burst churn profiles. Clients issue read-then-write operations over a mixed hot-key workload. Replication is delayed, and replicas maintain per-key sibling sets. Every version stores both the true causal history and the clock-encoded history so the analysis can measure metadata cost and semantic error separately.

Clock implementations:

- `vv`: exact client/session actor Version Vector
- `vv_vnode`: production-style replica actor Version Vector
- `dvv`: exact Dotted Version Vector
- `lease_dvv`: lease-pruned Dotted Version Vector

Primary metrics:

- metadata bytes per write
- actor entries per write
- ancestry precision and recall
- missed-conflict rate
- stale-sibling rate
- hot-key sibling pressure
- stale replica-actor fraction

## Early Smoke-Run Observation

A compact sustained-churn run with one seed and lease durations 8 and 16 produced the expected qualitative pattern:

- exact DVV had recall 1.0 and no stale-sibling error
- lease-DVV used fewer metadata bytes than DVV
- shorter leases reduced metadata further
- shorter leases also had lower recall and higher stale-sibling rate

These are smoke-test results only. They validate the pipeline shape, not the final empirical claim.

## Figures Planned

- Metadata cost by churn profile and clock
- History recall by churn profile and clock
- Missed-conflict and stale-sibling rates by churn profile and clock
- Hot-key sibling pressure over time
- Lease-DVV metadata reduction versus recall loss
- Lease duration ablation for metadata, recall, stale siblings, and pruning rate

## Experiments Still Needed

- Full 4-profile matrix with `vv`, `vv_vnode`, `dvv`, and at least three `lease_dvv` durations.
- Seed sweep large enough for stable averages. Minimum recommended: 5 seeds; preferred: 10 if runtime is acceptable.
- Lease-duration ablation normalized around observed network delay and churn rate.
- Sensitivity to replication factor, especially 3 versus 5.
- Sensitivity to hot-key probability and burst writer count.
- Optional anti-entropy/read-repair experiment if time allows.

## Current Caveats

- Metadata bytes are JSON payload serialization bytes with non-payload clock labels excluded, not a hand-optimized binary wire encoding.
- The simulator does not yet model CPU time, compare cost, storage compaction, or network bandwidth contention.
- Lease renewal is based on observing version stamps, not a separate membership heartbeat.
- The current report should phrase lease-DVV as an approximate tunable design, not a correctness-preserving replacement for exact DVV.
