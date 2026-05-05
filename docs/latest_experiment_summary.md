# Latest Experiment Summary

This is a quick reference for where `vv` vs `dvv` differences are actually visible in current outputs.

## Recommended interpretation (quick)

- Use **`vv` + `dvv` under the same `actor-domain`** to compare exactness:
  - both should keep `precision = 1.0` and `recall = 1.0`.
- DVV is **not universally smaller** than VV on raw JSON bytes.
- DVV usually helps when `actor-domain=client` and the number of distinct writers grows (higher active actor cardinality).
- When `actor-domain=physical` is the primary baseline, DVV and VV share the same actor set; DVV can be slightly heavier because of an explicit dot field in this implementation.

The main lookup file for exact comparisons is:

- `output/.../aggregate/comparison_by_clock.csv`
- columns: `metadata.avg_metadata_bytes`, `metadata.avg_actor_entries`, `accuracy.avg_precision`, `accuracy.avg_recall`

Use the following plots to inspect tradeoffs visually:

- `figures/metadata_bytes_vs_profile.png` for metadata by profile
- `figures/false_positive_negative_by_clock.png` for exactness side-effects
- `time_series/..._report/` for profile-level temporal behavior

## Where DVV is better than VV (current evidence)

### Client-domain stress on actor cardinality

Output directories:

- `output/experiments/sensitivity_client_count_32_2026-05-02_22-49-17/aggregate/comparison_by_clock.csv`
- `output/experiments/sensitivity_client_count_512_2026-05-02_22-49-29/aggregate/comparison_by_clock.csv`

| Scenario | Profile | VV avg bytes | DVV avg bytes | DVV gain |
|---|---|---:|---:|---:|
| client_count=32 | stable | 186.389 | 122.144 | 34.5% |
| client_count=32 | sustained | 192.614 | 145.477 | 24.5% |
| client_count=512 | stable | 331.289 | 120.857 | 63.5% |
| client_count=512 | sustained | 356.274 | 142.984 | 59.9% |

All rows keep exact behavior in these runs (`accuracy.avg_precision = 1.0`, `accuracy.avg_recall = 1.0`).

### Replication-factor sensitivity (supporting evidence)

Output directories:

- `output/experiments/sensitivity_rf3_2026-05-02_22-49-42/aggregate/comparison_by_clock.csv`
- `output/experiments/sensitivity_rf5_2026-05-02_22-49-54/aggregate/comparison_by_clock.csv`

| Scenario | Profile | VV avg bytes | DVV avg bytes | DVV gain |
|---|---|---:|---:|---:|
| RF=3 | stable | 271.517 | 127.476 | 53.0% |
| RF=3 | sustained | 312.208 | 136.652 | 56.2% |
| RF=5 | stable | 335.889 | 126.457 | 62.4% |
| RF=5 | sustained | 291.367 | 140.028 | 52.0% |

## Where DVV is not the winner (important)

### Physical-actor default final matrix

Output directory:

- `output/experiments/2026-05-05_16-31-59_per_object_clock_study_final/aggregate/comparison_by_clock.csv`
- `output/experiments/2026-05-05_16-35-06_per_object_clock_study_final/aggregate/comparison_by_clock.csv` (slot-domain variant)

In these runs, exact `vv` and `dvv` are both correct (`precision=1.0`, `recall=1.0`), but `dvv` is not lower on average metadata bytes; differences are small and can invert because of `DVV` encoding shape in this implementation.

## Why this happens

The simulator compares exact semantics and explicit metadata:

1. `metadata` is JSON serialization size of the encoded stamp, excluding type.
2. `VV` stores a per-actor summary vector.
3. `DVV` stores a summary vector plus an explicit current dot and optional exceptions.
4. If actor sets are very similar (e.g., physical actors in both variants), `VV` and `DVV` can be tied or `VV` can win on byte count.
5. The expected DVV advantage appears when actor-domain inflation would otherwise force `VV` to keep a larger vector for more client actors than needed by the dotted representation.

## Report-facing ordering suggestion

1. Start from exactness check: show `vv` vs `dvv` precision/recall (both 1.0 on controlled runs).
2. Show exactness-vs-metadata tradeoff table for high-actor-regime runs above.
3. Add lease-DVV plots (`lease_*`) only after this, to separate the approximation story.
