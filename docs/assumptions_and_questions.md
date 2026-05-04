# Assumptions and Open Questions

This file records modeling assumptions and questions that should be revisited before the final report is submitted.

## Current Assumptions

- The simulator studies per-object causality in a replicated key-value store, not general message-passing causality across arbitrary processes.
- A version's ground-truth history is the transitive closure of versions read before a write plus the write's own dot.
- Exact VV uses client/session actors. This is the fairness baseline for full ancestry, and it intentionally pays metadata proportional to distinct writers of an object.
- Vnode-VV uses replica/server actors. This is the production-realism baseline and intentionally exposes proxy-actor anomalies when one server coordinates independent clients.
- DVV and lease-DVV use replica/server dots. DVV should preserve object ancestry without falsely ordering independent writes from the same coordinator.
- Lease-DVV is approximate. It may prune old actor history after the lease expires, so reduced metadata is expected to trade against ancestry recall and stale sibling retention.
- Latency is network delivery latency only. The current simulator does not model CPU time for vector comparison, serialization, disk I/O, or garbage collection.
- Metadata bytes are serialized JSON payload bytes with the clock-type label excluded. This is useful for relative comparison, but the paper should describe it as a portable proxy rather than an optimized wire format.
- Churn means replica membership churn. Client/session actor churn is modeled through a large session pool and one-shot session IDs.
- Hot-key contention is deliberate. It is not meant to represent all production traffic; it is used to make sibling behavior and causality loss observable.

## Open Questions

- What is the exact final research claim for lease-DVV: lower metadata at bounded recall loss, lower stale actor pressure, or a tunable operating region?
- What lease durations should be included in the final ablation: fixed values such as 8/16/32, values normalized to replication latency, or values normalized to churn half-life?
- Should the final paper include Interval Tree Clocks or Bounded Version Vectors as implemented baselines, or only as literature comparisons?
- Should we add a CPU-cost proxy, such as comparisons per write and serialized component count, to support the "speed" part of the claim?
- What report-ready number of seeds is acceptable for the course project: 3, 5, or 10?
- Should the main figures use all churn profiles or focus on sustained and burst churn where the lease-DVV contribution is clearest?
- Do we want to model anti-entropy/read-repair separately from write-time replication, or keep the current partial replication model?
- Should lease renewal be tied only to observing stamps, as implemented now, or also to heartbeats/membership liveness?
- Should leaving replicas ever rejoin with the same actor ID, or should every join be a fresh actor as currently modeled?
- Do we need a production-inspired fixed vnode count per physical node, or is one actor per active replica sufficient for this report?
