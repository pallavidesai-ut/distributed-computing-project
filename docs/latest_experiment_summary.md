# Latest Experiment Summary

This summarizes the organized experiment runs generated after adding report-ready plots, sensitivity configs, and the deterministic same-replica DVV example.

## Main final run

Output directory:

```text
output/experiments/2026-05-03_02-03-07_per_object_clock_study_final/
```

The run uses the organized layout:

- `manifest.json`
- `config/`
- `aggregate/`
- `figures/`
- `time_series/`
- `runs/`

Report-ready plots are emitted as both PNG and PDF. Aggregate plots include standard-error bars across seeds. The deterministic DVV-vs-vnode example is available at:

```text
figures/same_replica_concurrency_example.pdf
aggregate/same_replica_concurrency_example.csv
```

## Main result

Exact DVV preserved exact VV's ancestry fidelity while using much less metadata.

| Profile | VV avg bytes | DVV avg bytes | DVV reduction vs VV |
|---|---:|---:|---:|
| stable | 294.7 | 123.4 | 58.1% |
| low | 318.1 | 126.8 | 60.1% |
| sustained | 308.9 | 139.2 | 54.9% |
| burst | 312.7 | 151.1 | 51.7% |

Exact-clock validation:

- `vv`: precision 1.0, recall 1.0 in all profiles.
- `dvv`: precision 1.0, recall 1.0 in all profiles.

Coarse vnode-VV behavior:

| Profile | `vv_vnode` precision | `vv_vnode` missed-conflict rate |
|---|---:|---:|
| stable | 0.8331 | 0.1935 |
| low | 0.8530 | 0.2184 |
| sustained | 0.8377 | 0.2027 |
| burst | 0.8417 | 0.2318 |

Interpretation: `vv_vnode` is compact but semantically coarse. It over-represents ancestry and misses conflicts, so it should be framed as a production-style baseline rather than exact VV.

## Sensitivity runs

Generated outputs:

```text
output/experiments/2026-05-03_02-03-58_sensitivity_client_count_32/
output/experiments/2026-05-03_02-04-12_sensitivity_client_count_512/
output/experiments/2026-05-03_02-04-27_sensitivity_rf3/
output/experiments/2026-05-03_02-04-41_sensitivity_rf5/
```

### Client-count sensitivity

| Scenario | Profile | VV avg bytes | DVV avg bytes | DVV reduction vs VV |
|---|---|---:|---:|---:|
| 32 clients | stable | 186.4 | 122.1 | 34.5% |
| 32 clients | sustained | 192.6 | 145.5 | 24.5% |
| 512 clients | stable | 331.3 | 120.9 | 63.5% |
| 512 clients | sustained | 356.3 | 143.0 | 59.9% |

Interpretation: this strongly supports the actor-cardinality story. Exact VV becomes more expensive as the number of possible client actors increases, while DVV remains much less sensitive because it uses replica-issued dots.

### Replication-factor sensitivity

| Scenario | Profile | VV avg bytes | DVV avg bytes | DVV reduction vs VV |
|---|---|---:|---:|---:|
| RF=3 | stable | 271.5 | 127.5 | 53.1% |
| RF=3 | sustained | 312.2 | 136.7 | 56.2% |
| RF=5 | stable | 335.9 | 126.5 | 62.4% |
| RF=5 | sustained | 291.4 | 140.0 | 51.9% |

Interpretation: DVV remains substantially smaller than VV across replication factors. The exact magnitude varies with visibility/concurrency, but the qualitative result is stable.

## Extreme stress runs

Generated outputs:

```text
output/experiments/2026-05-03_02-05-02_extreme_hotspot_churn/
output/experiments/2026-05-03_02-05-22_extreme_sparse_replication/
```

| Scenario | Profile | VV avg bytes | DVV avg bytes | DVV reduction vs VV |
|---|---|---:|---:|---:|
| hotspot churn | burst | 882.1 | 219.7 | 75.1% |
| hotspot churn | sustained | 934.9 | 193.1 | 79.3% |
| sparse replication | low | 532.9 | 159.7 | 70.0% |
| sparse replication | sustained | 498.1 | 159.6 | 68.0% |

Interpretation: the stress scenarios amplify the paper's main story. When hot-key contention, client cardinality, and/or sparse replication increase, exact VV grows quickly while exact DVV remains much smaller. These scenarios should be reported as stress tests or appendix evidence, not as the primary balanced workload.

## Report-ready figure notes

Use PDF figures for the LaTeX report when possible:

- `figures/metadata_bytes_vs_profile.pdf`
- `figures/recall_vs_profile.pdf`
- `figures/metadata_reduction_vs_recall_loss.pdf`
- `figures/lease_ablation_metadata.pdf`
- `figures/lease_ablation_recall.pdf`
- `figures/same_replica_concurrency_example.pdf`
- `time_series/metadata_bytes_over_time_report.pdf`
- `time_series/recall_over_time_report.pdf`

The main body should focus on:

1. DVV vs exact VV metadata reduction with perfect precision/recall.
2. Lease-DVV metadata/recall tradeoff.
3. Same-replica concurrency example showing why dots matter.
4. Sensitivity to client count as evidence that the metadata advantage comes from actor cardinality.

Use `vv_vnode` and extreme scenarios as secondary/appendix material.
