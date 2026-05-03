# AGENTS.md

Guidance for coding agents working in this repository.

## Project purpose

This repo contains a per-object causality simulator for comparing exact Version Vectors (`vv`), exact Dotted Version Vectors (`dvv`), lease-pruned DVV variants (`lease_dvv`), and a coarse vnode/replica Version Vector baseline (`vv_vnode`) under churn.

The paper claim depends on preserving this framing:

- `vv` is the exact client-actor baseline.
- `dvv` is exact and should match `vv` on ancestry precision/recall.
- `lease_dvv` is intentionally approximate and trades metadata for recall.
- `vv_vnode` is a compact production-style coarse baseline, not a fair exact VV baseline.

Do not describe vanilla VV as lossy. Lossiness comes from coarse actor granularity or lease pruning.

## Main commands

Run tests:

```bash
pytest -q
```

Run one simulation:

```bash
python simulate.py --clock lease_dvv --profile sustained --run-name lease_sustained
```

Run the paper/reproducibility workflow:

```bash
scripts/reproduce_final.sh
```

Run a quick smoke matrix:

```bash
scripts/reproduce_final.sh configs/final_study.yaml \
  --profiles stable --clocks vv dvv --seeds 1 --sim-time 10
```

The final workflow writes timestamped results under:

```text
output/experiments/per_object_clock_study_final_<timestamp>/
```

## Result handling

`output/` is ignored by git. Do not commit full experiment outputs by default.

For paper review of a run, create a Markdown summary inside the result directory, e.g.:

```text
output/experiments/<run>/plot_interpretation.md
```

If final figures/tables must be versioned, copy only selected publication artifacts to a tracked docs/paper artifact directory in a separate, explicit commit.

## Important files

- `clocksim/context.py`: causal contexts, dots, event IDs, context comparison.
- `clocksim/clocks.py`: VV, vnode-VV, DVV, and lease-DVV implementations.
- `clocksim/store.py`: version records, sibling application, conflict decisions.
- `clocksim/sim.py`: discrete-event cluster/workload simulation.
- `clocksim/metrics.py`: metrics collection and summaries.
- `simulate.py`: single-run CLI wrapper.
- `run_experiments.py`: experiment matrix, aggregate plots, report artifacts.
- `analyze_run.py`: per-run analysis and plots.
- `configs/final_study.yaml`: publication-oriented experiment configuration.
- `scripts/reproduce_final.sh`: main timestamped experiment runner.
- `docs/paper_plan.md`: publication plan.
- `docs/results_workflow.md`: results organization guidance.

## Testing expectations

Before committing, run:

```bash
pytest -q
```

Tests should verify:

- exact VV/DVV preserve ancestry in smoke scenarios;
- metric definitions stay aligned between `MetricsCollector.summary()` and `analyze_run.py`;
- lease-DVV pruning/recall tradeoffs are intentional;
- `vv_vnode` is treated as coarse/lossy where appropriate.

## Coding guidelines

- Keep changes small and targeted.
- Prefer adding deterministic tests for semantic changes.
- Avoid committing generated caches, build output, or large result directories.
- Preserve timestamped output behavior in `scripts/reproduce_final.sh`.
- Keep README commands in sync with the actual workflow.

## Paper-facing interpretation rules

When writing docs or reports:

- Say exact VV is correct but metadata-heavy.
- Say exact DVV preserves correctness with lower metadata in this workload.
- Say lease-DVV is approximate and exposes a metadata/recall knob.
- Say `vv_vnode` is compact but semantically coarse, causing false ancestry/missed conflicts.
- Mention that metadata bytes are JSON serialization bytes, not optimized binary wire sizes.
