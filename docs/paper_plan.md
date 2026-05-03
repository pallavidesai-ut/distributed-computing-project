# Paper Plan: Leveraging the Per-Object Clock Simulator for a Publication-Ready Paper

## Working Title

**Exact and Approximate Per-Object Causality Metadata Under Replica Churn**

Alternative titles:

- **Dotted Version Vectors Under Churn: Metadata Savings and Accuracy Tradeoffs**
- **Causal Metadata Growth in Dynamic Replicated Stores: VV, DVV, and Lease-Pruned DVV**
- **Tunable Per-Object Causality: A Simulation Study of Version Vectors and Lease-Pruned Dots**

## One-Sentence Thesis

Exact Dotted Version Vectors preserve the same per-object ancestry correctness as exact Version Vectors while using substantially less metadata under realistic replicated-key-value workloads, and lease-pruned DVVs expose a tunable metadata-versus-recall tradeoff under churn.

## Positioning From the Deep Research Report

The recent literature shows that vector clocks remain the semantic gold standard for exact causality, but modern systems avoid global full-width vectors by specializing or weakening the contract. The simulator should be positioned inside this design space:

1. **Exact but scoped causality**: Version vectors and dotted version vectors track per-object causal histories rather than global process histories.
2. **Dynamic membership pressure**: Churn causes actor metadata to accumulate or become stale, especially when object histories outlive the replicas or sessions that produced them.
3. **Approximate/tunable mechanisms**: Lease-DVV is analogous in spirit to Bloom clocks, HLC/PWC-style mechanisms, and tunable causal consistency: it trades full precision for smaller metadata under configurable assumptions.
4. **Benchmarking gap**: The literature lacks shared empirical benchmarks for comparing clock mechanisms under the same churn, workload, and partial-replication assumptions. This simulator can contribute a focused benchmark and measurement methodology.

The paper should not claim to invent a universal vector-clock replacement. It should claim to provide a controlled empirical comparison of exact and approximate per-object causal metadata under churn.

## Target Contribution Claims

### Primary Contribution

A simulation framework that separates **ground-truth causal history** from **clock-encoded history**, enabling direct measurement of both metadata cost and semantic accuracy for per-object clocks.

### Empirical Contribution

A controlled comparison of:

- `vv`: exact client-actor Version Vector
- `dvv`: exact Dotted Version Vector
- `lease_dvv_L8`, `lease_dvv_L16`, `lease_dvv_L32`: approximate lease-pruned DVV variants
- `vv_vnode`: coarse replica/vnode actor Version Vector, treated as a production-style baseline or appendix result

### Main Result Shape

- Exact VV and exact DVV both achieve perfect ancestry precision and recall.
- DVV achieves the same correctness as VV with lower metadata.
- Lease-DVV reduces metadata further, but introduces recall loss and stale-sibling risk.
- Vnode-VV can be compact, but it is semantically coarse under client-causality ground truth and should not be described as exact VV.

## Corrected Framing

Avoid this incorrect claim:

> Vanilla VV is lossy or ambiguous.

Use this instead:

> Exact VV is correct but metadata-heavy when the actor set is the client/session writer set. DVV preserves exact per-object ancestry with lower metadata by separating each version's unique dot from its causal context. Coarse vnode-level VV is compact but loses information relative to per-client causal ground truth.

## Paper Outline

### 1. Introduction

Goals:

- Motivate causal metadata in available replicated stores.
- Explain why churn and partial replication make metadata growth important.
- Position vector clocks as exact but potentially expensive.
- Introduce DVVs and lease-pruned DVVs as scoped and tunable alternatives.
- State the paper's research questions.

Research questions:

1. Can exact DVV match exact VV's causal-history fidelity while reducing per-object metadata?
2. How does churn affect metadata growth, stale actor entries, and sibling pressure?
3. How much metadata can lease-DVV save, and what recall/conflict-resolution cost does it incur?
4. How does a coarse production-style vnode VV compare to exact client-actor VV and DVV?

Expected introduction close:

> We find that exact DVV preserves VV's precision and recall while reducing average metadata by roughly 52--60% across churn profiles in our fair client-actor workload. Lease-pruned DVV provides additional savings at the cost of measurable recall loss, making lease duration an explicit operating knob rather than a correctness-preserving optimization.

### 2. Background and Related Work

Use the deep research report to structure this section.

Subsections:

1. **Logical clocks and exact causality**
   - Lamport clocks
   - Vector clocks
   - Charron-Bost/lower-bound intuition: exact causality has inherent dimensional cost

2. **Version vectors and dotted version vectors**
   - Version vectors for replicated objects
   - Dotted Version Vectors, Riak-style object conflict tracking
   - Distinction between actor granularity: client/session actors vs replica/vnode actors

3. **Dynamic membership and scoped causality**
   - Interval Tree Clocks as dynamic-membership comparator
   - Tree clocks as exact data-structure optimization
   - CausalMesh/TCC-like scoped causal systems

4. **Approximate and tunable causality**
   - Bloom clocks
   - HLC/PWC scalar approaches
   - Tunable causal consistency
   - Position lease-DVV as a per-object approximation with explicit recall tradeoff

5. **Benchmarking gap**
   - Recent work uses incomparable workloads/metrics.
   - This paper contributes a focused simulator for comparing per-object clock metadata under churn.

### 3. Simulator Design

Describe the implemented simulator, not the older causal-broadcast/buffering design.

Key points:

- Discrete-event simulator.
- Partially replicated key-value store.
- Each key maps to a sibling set of versions.
- Writes are read-then-write operations.
- Each version records:
  - clock stamp
  - origin replica
  - creation time
  - read set size
  - phase/background/burst/merge
  - true causal history as event IDs
- Replication is delayed and partial.
- Replicas compare incoming versions against local siblings.
- The simulator records both true relation and clock relation for every conflict-resolution decision.

Important design novelty:

> The simulator maintains ground-truth ancestry separately from clock metadata. This makes it possible to measure false positives, false negatives, precision, and recall of the clock representation rather than only measuring metadata size.

### 4. Clock Models

#### Exact VV

- Actor: bounded client/session ID.
- Context: vector over client actors.
- Correctness expectation: exact ancestry precision and recall.
- Cost expectation: grows with number of distinct clients writing an object.

#### Exact DVV

- Actor: replica ID issuing a dot.
- Stamp: summary/context plus new dot.
- Correctness expectation: exact ancestry precision and recall.
- Cost expectation: lower than client-actor VV when writer cardinality exceeds replica cardinality/context width.

#### Lease-DVV

- Same DVV representation but prunes expired actors/events before issuing new writes.
- Lease is renewed when stamps are observed.
- Correctness expectation: may lose ancestry recall.
- Cost expectation: smaller metadata, especially under churn.

#### Vnode-VV

- Actor: replica/vnode ID.
- Compact but coarse.
- Use as production-style baseline, not as exact VV.
- Report separately or in appendix.

### 5. Workloads and Experimental Setup

Required setup table:

| Parameter | Value(s) | Rationale |
|---|---:|---|
| Churn profiles | stable, low, sustained, burst | cover fixed, gradual, continuous, and shock membership changes |
| Clock variants | vv, dvv, lease_dvv_L8/L16/L32, vv_vnode | exact, approximate, production-style baselines |
| Seeds | ideally 5--10 | confidence/stability |
| Key count | current configured value | mixed hot/cold workload |
| Hot-key probability | current configured value | create contention and sibling pressure |
| Client count | current configured value | expose VV actor-cardinality cost |
| Replication factor | current configured value; sensitivity 3 vs 5 if time | partial replication pressure |
| Network latency | min/max configured range | delayed replication/concurrency |
| Burst writers | current configured value; sensitivity if time | hot-key concurrency |
| Lease durations | 8, 16, 32 or normalized alternatives | tradeoff curve |

Minimum publication-ready experiment matrix:

- 4 profiles × 6 clock variants × 5 seeds.

Preferred matrix:

- 4 profiles × 6 clock variants × 10 seeds.
- Add sensitivity studies for replication factor and hot-key probability.

## Metrics

### Primary Metrics

#### Metadata Cost

- Average metadata bytes per write
- p95 metadata bytes per write
- Average metadata components
- Average actor entries per write

Purpose:

- Quantify space overhead of each clock.
- Main evidence for DVV/lease-DVV savings.

#### Ancestry Fidelity

- Average history precision
- Average history recall
- False positive events per version
- False negative events per version

Definitions:

- Precision: fraction of clock-represented events that are truly in the version's causal history.
- Recall: fraction of true causal-history events represented by the clock.

Interpretation:

- Exact VV and exact DVV should be 1.0/1.0.
- Vnode-VV may show false positives.
- Lease-DVV may show false negatives/recall loss.

#### Conflict-Resolution Quality

- Missed-conflict rate
- Stale-sibling rate
- Concurrent pair count

Interpretation:

- Missed conflicts are dangerous because concurrent writes are collapsed.
- Stale siblings indicate lost domination knowledge and extra application-level resolution burden.

#### Replica State Pressure

- Average hot-key sibling count
- p95 hot-key sibling count
- Average versions per key
- Stale replica-actor fraction

Purpose:

- Show operational consequences of metadata/accuracy choices.

### Secondary Metrics

- Replication latency as a sanity check, not a central claim.
- Logical write throughput as a workload consistency check, not a clock-performance result.
- Pruned write rate for lease-DVV.
- Pruned actors/events per write.

## Figures and Tables

### Must-Have Figures

1. **Metadata bytes by profile and clock**
   - Bar chart.
   - X-axis: profile.
   - Groups/colors: `vv`, `dvv`, `lease_dvv_L8`, `lease_dvv_L16`, `lease_dvv_L32`.
   - Optional appendix: include `vv_vnode`.
   - Claim: DVV reduces exact metadata; lease-DVV reduces further.

2. **Actor entries by profile and clock**
   - Bar chart.
   - Shows why VV is heavier: more client actors in hot-object histories.

3. **History recall by profile and clock**
   - Bar chart or dot plot.
   - Exact VV and DVV should be 1.0.
   - Lease-DVV should show lease-dependent recall loss.

4. **Metadata reduction versus recall loss**
   - Scatter plot.
   - X-axis: metadata reduction vs DVV or VV.
   - Y-axis: recall loss.
   - Points: profile + lease duration.
   - This is the key lease-DVV tradeoff figure.

5. **Lease-duration ablation**
   - Line plot with lease duration on x-axis.
   - Separate panels or y-axes:
     - average metadata bytes
     - average recall
     - stale-sibling rate
     - pruned-write rate

6. **Hot-key sibling pressure over time**
   - Time-series plot.
   - Shows operational impact of stale/missed ancestry.

### Must-Have Tables

1. **Experiment configuration table**
   - Workload parameters, profiles, seeds, clocks, lease durations.

2. **Headline results table**
   - Rows: profile.
   - Columns:
     - VV avg bytes
     - DVV avg bytes
     - DVV reduction vs VV
     - DVV precision/recall
     - best lease-DVV bytes
     - best lease-DVV recall

3. **Clock semantics table**
   - Rows: VV, DVV, Lease-DVV, Vnode-VV.
   - Columns:
     - actor granularity
     - exact/approximate
     - metadata shape
     - expected failure mode

4. **Threats/caveats table**
   - Synthetic workload
   - JSON byte accounting
   - no CPU/network contention
   - no real membership heartbeat
   - object-level causality only

### Appendix Figures

- Vnode-VV precision/false-positive comparison.
- p95 metadata bytes.
- Stale actor fraction over time.
- Sensitivity to replication factor.
- Sensitivity to hot-key probability or burst writers.

## Current Headline Numbers to Preserve

From the fair VV run:

- DVV metadata reduction versus exact VV:
  - stable: **59.8%**
  - low: **56.6%**
  - sustained: **55.7%**
  - burst: **52.2%**
- Exact clock validation:
  - `vv`: precision **1.000**, recall **1.000**
  - `dvv`: precision **1.000**, recall **1.000**
- Vnode-VV:
  - average precision about **0.807**
  - false positives around **9522**
  - should be treated as coarse/lossy, not as vanilla VV

These numbers should be regenerated or verified after final cleanup and included with exact run metadata.

## Threats to Validity

### Internal Validity

- Clock correctness depends on the simulator's implementation of true causal history.
- Random seeds may affect contention and churn timing.
- JSON metadata bytes may exaggerate some representation overheads.

### External Validity

- Workload is synthetic, not a production trace.
- The simulator does not model CPU cost, serialization/deserialization cost, bandwidth contention, storage compaction, or application-level conflict resolution.
- Lease renewal is based on observing version stamps, not a production membership/heartbeat subsystem.
- Results apply to per-object causality, not full transactional causal consistency.

### Construct Validity

- `vv` and `dvv` use different actor granularities by design: client actors for exact VV and replica-issued dots for DVV. The paper must justify this as a comparison of exact object causality costs under realistic writer vs replica cardinalities.
- `vv_vnode` is not a fair exact baseline; it is a production-style coarse actor-granularity baseline.

## Related Work Integration Plan

Use the deep research report as follows:

| Topic | Use in Paper |
|---|---|
| Lamport clocks | Explain scalar clocks and why they cannot distinguish concurrency |
| Vector clocks | Semantic reference model for exact causality |
| Charron-Bost/lower bound | Motivate why exact causality has inherent dimensional cost |
| Version vectors | Connect to replicated object histories |
| Dotted Version Vectors / Riak | Closest direct related work and practical motivation |
| Interval Tree Clocks | Dynamic-membership alternative, not implemented |
| Bloom clocks | Approximate/tunable metadata comparison point |
| HLC/PWC | Compact scalar alternatives that weaken exact concurrency detection |
| Tree clocks | Exact data-structure optimization; different target domain |
| CausalMesh/TCC/Eiger-style systems | Modern scoped/verified causal consistency systems |

## Publication-Ready Next Steps

### Step 1: Stabilize Code and Reproducibility

- Ensure `simulate.py`, `run_experiments.py`, and `analyze_run.py` run cleanly after cleanup.
- Commit the rename from `code.py` to `simulate.py`.
- Add a `reproduce.sh` or `Makefile` target for the final experiment matrix.
- Save final experiment config JSON with all report outputs.

### Step 2: Regenerate Final Results

Run the final fair matrix:

```bash
python run_experiments.py \
  --experiment-name per_object_clock_study_final \
  --clocks vv dvv lease_dvv vv_vnode \
  --lease-durations 8 16 32 \
  --profiles stable low sustained burst \
  --seeds 1 2 3 4 5
```

Preferred if runtime allows:

```bash
python run_experiments.py \
  --experiment-name per_object_clock_study_final_10seed \
  --clocks vv dvv lease_dvv vv_vnode \
  --lease-durations 8 16 32 \
  --profiles stable low sustained burst \
  --seeds 1 2 3 4 5 6 7 8 9 10
```

### Step 3: Validate Exact Clocks

Before writing final claims, check:

- `vv` average precision = 1.0
- `vv` average recall = 1.0
- `dvv` average precision = 1.0
- `dvv` average recall = 1.0
- no false positives/false negatives for exact clocks

If any exact clock deviates, stop and debug before finalizing the paper.

### Step 4: Build Final Figures

Use generated artifacts under:

```text
output/experiments/per_object_clock_study_final/
```

Pull figures into `docs/figures/` or reference them directly from the LaTeX file.

Required figure filenames to standardize:

- `fig_metadata_by_profile.pdf`
- `fig_actor_entries_by_profile.pdf`
- `fig_recall_by_profile.pdf`
- `fig_metadata_reduction_vs_recall_loss.pdf`
- `fig_lease_ablation.pdf`
- `fig_hot_key_siblings_over_time.pdf`

### Step 5: Rewrite `docs/report_draft.tex`

Replace stale causal-broadcast/buffering language with current simulator language.

Sections to rewrite completely:

- Abstract
- Core Idea
- Motivation
- Design
- Experimental Setup and Results
- Conclusion

Add sections if space allows:

- Background and Related Work
- Threats to Validity

### Step 6: Bibliography Cleanup

Convert the deep research report's placeholder citation markers into BibTeX entries. Minimum references:

- Lamport logical clocks
- Vector clocks / Fidge-Mattern or equivalent
- Charron-Bost lower bound
- Dotted Version Vectors
- Riak DVV documentation or paper
- Interval Tree Clocks
- Bloom clocks
- Tree clocks
- Hybrid Logical Clocks
- One recent causal consistency/verified system paper, e.g. CausalMesh or Eiger-PORT+

### Step 7: Add Reproducibility Appendix

Include:

- Git commit hash
- Python version
- command line used for final experiment
- random seeds
- path to generated CSVs
- note that metadata bytes are JSON serialization bytes excluding the clock type label

## Additional Sensitivity and Stress Configurations

The repo includes optional configs for report-strengthening follow-up experiments.

### Client-count sensitivity

Purpose: show that exact client-actor VV becomes more expensive as writer cardinality increases, while DVV is less sensitive because dots are replica-issued.

Configs:

- `configs/sensitivity_client_count_32.yaml`: low writer-cardinality comparison.
- `configs/sensitivity_client_count_512.yaml`: high writer-cardinality comparison.

Run both, plus replication-factor sensitivity, with:

```bash
scripts/reproduce_sensitivity.sh
```

### Replication-factor sensitivity

Purpose: test how partial-replication fanout changes context width, visibility, and sibling pressure.

Configs:

- `configs/sensitivity_rf3.yaml`: lower fanout than the main RF=4 study.
- `configs/sensitivity_rf5.yaml`: higher fanout than the main RF=4 study.

### Extreme stress scenarios

Purpose: make qualitative differences more visible for report discussion and appendix figures.

Configs:

- `configs/extreme_hotspot_churn.yaml`: high hot-key contention, many clients, more burst writers, and sustained/burst churn. This should amplify VV growth, lease pruning, and stale-sibling pressure.
- `configs/extreme_sparse_replication.yaml`: low replication factor, many clients, higher latency, and weaker merge visibility. This should amplify divergence and conflict-resolution differences.

Run both with:

```bash
scripts/reproduce_extremes.sh
```

These stress scenarios should be reported as stress tests, not as the primary balanced workload.

## Draft Abstract Template

> Exact causality tracking remains difficult to scale because vector-clock metadata grows with the number of tracked actors. In replicated key-value stores, this pressure is amplified by churn and hot-object contention, where object histories can outlive the sessions or replicas that created them. We present a discrete-event simulator for per-object causality metadata that records both ground-truth read/write ancestry and clock-encoded ancestry, enabling direct measurement of metadata cost and semantic error. We compare exact client-actor Version Vectors, exact Dotted Version Vectors, lease-pruned Dotted Version Vectors, and a coarse vnode Version Vector baseline across stable, low, sustained, and burst churn profiles. In our fair client-actor workload, exact DVV preserves VV's perfect ancestry precision and recall while reducing average metadata by roughly 52--60% across churn profiles. Lease-pruned DVV reduces metadata further but introduces recall loss and stale-sibling risk, exposing an explicit metadata-versus-correctness operating curve. These results support treating DVV as an exact per-object metadata optimization and lease-DVV as an approximate tunable mechanism rather than a correctness-preserving replacement.

## Draft Conclusion Template

> This study reinforces the standard lesson that exact causality has an unavoidable metadata cost, but shows that actor granularity and object-level representation strongly affect that cost in practice. Exact client-actor Version Vectors and exact Dotted Version Vectors both preserved the simulated ground-truth ancestry, but DVV achieved substantially lower metadata by representing the current write as a dot plus compact context. Lease-pruned DVV provided an additional reduction mechanism under churn, but only by giving up some ancestry recall and increasing the risk of stale siblings. Finally, the vnode Version Vector baseline illustrates the danger of conflating compactness with correctness: coarse replica actors can reduce metadata while introducing false ancestry under per-client ground truth. Overall, the simulator provides a reproducible benchmark for studying scoped causal metadata and suggests that future systems should expose causality precision as an explicit design parameter rather than hiding it inside actor naming or ad hoc pruning policies.

## Immediate TODO Checklist

- [x] Regenerate final experiment matrix after code cleanup.
- [x] Verify exact VV and DVV precision/recall are 1.0.
- [x] Export final plots as PNG for report review.
- [x] Add simulator methodology documentation.
- [ ] Add error bars or confidence intervals to aggregate plots.
- [ ] Run replication-factor sensitivity, especially 3 vs 5.
- [ ] Run client-count sensitivity to emphasize VV actor-cardinality cost.
- [ ] Add deterministic same-replica concurrency example showing DVV vs `vv_vnode`.
- [ ] Rewrite `docs/report_draft.tex` using current simulator design.
- [ ] Add real BibTeX citations from the deep research report.
- [ ] Add threats-to-validity section.
- [ ] Add reproducibility appendix or paragraph.
- [ ] Decide whether `vv_vnode` appears in main results or appendix.
