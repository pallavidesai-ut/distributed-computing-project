# AGENTS.md

Guidance for coding agents working in this repository.

## Project purpose

This repo contains a per-object causality simulator for comparing exact Version Vectors (`vv`), exact Dotted Version Vectors (`dvv`), Interval Tree Clocks (`itc` when selected), and lease-pruned DVV variants (`lease_dvv`, `membership_lease_dvv`) under churn.

The report framing depends on keeping these ideas separate:

- Clock metadata is **per object/version**.
- The causal actor domain is configurable with `--actor-domain physical|slot|client`.
  - `physical`: churn-created node identities (`n0001`, `n0002`, ...); default and main high-churn/node-metadata study.
  - `slot`: stable logical replica/vnode slots (`r0001`, `r0002`, ...); production-style bounded actor sensitivity.
  - `client`: client/session actors (`c0001`, `c0002`, ...); client-causality/ITC sensitivity.
- Exact clocks (`vv`, `dvv`, `itc`) should preserve ancestry precision/recall when run over the same actor domain.
- `lease_dvv` and related lease variants are intentionally approximate and trade metadata for ancestry recall/stale-sibling behavior.
- Do not describe vanilla VV as lossy. Lossiness comes from pruning or intentionally coarse/mismatched actor granularity.

For the final report, the main high-churn story should default to `actor-domain: physical`, with `slot` and `client` studies treated as sensitivity/secondary analyses.

## Main commands

Run tests:

```bash
pytest -q
```

Run one simulation:

```bash
python simulate.py --clock lease_dvv --profile sustained --actor-domain physical --run-name lease_sustained
```

Run the paper/reproducibility workflow:

```bash
scripts/reproduce_final.sh configs/final_study.yaml
```

Run a quick smoke matrix:

```bash
scripts/reproduce_final.sh configs/final_study.yaml \
  --profiles stable --clocks vv dvv lease_dvv --seeds 1 --sim-time 10 \
  --jobs 2 --progress --fixed-lease-duration
```

The final workflow writes timestamped results under:

```text
output/experiments/<timestamp>_per_object_clock_study_final/
```

## Result handling

`output/` is ignored by git. Do not commit full experiment outputs by default.

For paper review of a run, create a Markdown summary inside the result directory, e.g.:

```text
output/experiments/<run>/plot_interpretation.md
```

If final figures/tables must be versioned, copy only selected publication artifacts to a tracked docs/paper artifact directory in a separate, explicit commit.

## Important files

- `clocksim/config.py`: shared scenario config dataclasses and CLI/config helpers, including `actor_domain` and key distribution settings.
- `clocksim/context.py`: causal contexts, dots, event IDs, context comparison.
- `clocksim/clocks.py`: VV, DVV, ITC, lease-DVV, and membership-lease implementations.
- `clocksim/store.py`: version records, sibling application, conflict decisions.
- `clocksim/sim.py`: discrete-event cluster/workload simulation and actor-domain selection.
- `clocksim/metrics.py`: metrics collection and summaries.
- `simulate.py`: single-run CLI wrapper.
- `run_experiments.py`: experiment matrix, parallel execution, aggregate plots, report artifacts.
- `analyze_run.py`: per-run analysis and plots.
- `configs/final_study.yaml`: publication-oriented experiment configuration.
- `configs/client_actor_clock_study.yaml`: client/session actor-domain sensitivity.
- `configs/itc_extreme_dynamic_clients.yaml`: ITC/client-domain stress study.
- `scripts/reproduce_final.sh`: main timestamped experiment runner and output organizer.
- `docs/report/`: report source notes.
- `docs/simulator_methodology.md`: simulator methodology.
- `docs/results_workflow.md`: results organization guidance.

## Required agent workflow

Follow this order for every non-trivial change:

1. **Update**
   - Make the requested code, config, script, test, or documentation changes.
   - Keep edits focused on the user's request.
   - Do not overwrite unrelated user work.

2. **Run tests**
   - Run the full test suite before claiming the work is complete:

   ```bash
   pytest -q
   ```

   - If the change touches the experiment workflow, also run a small smoke matrix instead of the full final matrix:

   ```bash
   scripts/reproduce_final.sh configs/final_study.yaml \
     --profiles stable --clocks vv dvv lease_dvv --seeds 1 --sim-time 10 \
     --jobs 2 --progress --fixed-lease-duration
   ```

3. **Verify outputs**
   - Check that expected files were produced.
   - For experiment workflow changes, verify at least:
     - `manifest.json`
     - `config/experiment_config.json`
     - `aggregate/comparison_by_clock.csv`
     - `aggregate/comparison_runs.csv`
     - one aggregate plot such as `figures/metadata_bytes_vs_profile.png`
     - `time_series/` plots when relevant
     - per-run files under `runs/<run_name>/raw/` and `runs/<run_name>/analysis/`
   - Remove transient caches such as `__pycache__/` before committing.

4. **Summarize results to the user**
   - Tell the user exactly what changed.
   - Report test results.
   - If experiments were run, provide the output directory and summarize what the plots/results show.
   - Ask for confirmation before committing if the user has not already requested a commit/push.

5. **Commit and push only when requested or after confirmation**
   - Inspect `git status --short`.
   - Stage only intended files.
   - Commit with a concise message.
   - Push to the current working branch.
   - Report the commit hash and push target.

## Testing expectations

Before committing, run:

```bash
pytest -q
```

Tests should verify:

- exact VV/DVV/ITC preserve ancestry in smoke scenarios;
- metric definitions stay aligned between `MetricsCollector.summary()` and `analyze_run.py`;
- lease-DVV pruning/recall tradeoffs are intentional;
- actor-domain selection (`physical`, `slot`, `client`) routes clock actor IDs correctly;
- key-distribution behavior remains deterministic enough for smoke tests.

## Coding guidelines

- Keep changes small and targeted.
- Prefer adding deterministic tests for semantic changes.
- Avoid committing generated caches, build output, or large result directories.
- Preserve timestamped output behavior in `scripts/reproduce_final.sh`.
- Keep README/config/report commands in sync with the actual workflow.
- For multiprocessing changes, keep workers isolated and leave aggregate CSV/plot/report generation in the parent process.

## Paper-facing interpretation rules

When writing docs or reports:

- Say exact VV and exact DVV are correct over the selected actor domain.
- For the main `physical` actor-domain study, say metadata growth reflects cumulative churn-created node actors in per-object histories.
- Say exact DVV preserves correctness; its metadata advantage depends on actor domain, churn, and serialization overhead.
- Say lease-DVV is approximate and exposes a metadata/recall/stale-sibling knob.
- Use `slot` results to discuss production-style stable vnode/replica identities.
- Use `client` results to discuss session/client causal tracking and ITC comparisons.
- Mention that metadata bytes are JSON serialization bytes, not optimized binary wire sizes.
