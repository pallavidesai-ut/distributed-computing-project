# Results Workflow

## What the experiment runner produces

`run_experiments.py` generates both tabular results and plots. Each run directory contains raw CSVs, configs, summaries, and per-run analysis plots. The top-level experiment directory contains aggregate CSVs, report notes, and comparison plots.

Important top-level outputs:

- `experiment_config.json`
- `comparison_runs.csv`
- `comparison_by_clock.csv`
- `lease_duration_ablation.csv`
- `metadata_reduction_vs_recall_loss.csv`
- `study_report.md`
- `*_vs_profile.png`
- `metadata_reduction_vs_recall_loss.png`
- `lease_ablation_*.png`
- `time_series_report/*.csv`
- `time_series_report/*.png`

## Timestamped runs

Use the wrapper script:

```bash
scripts/reproduce_final.sh
```

By default this reads `configs/final_study.yaml` and writes to a timestamped experiment name such as:

```text
output/experiments/per_object_clock_study_final_2026-05-02_22-21-26/
```

Use a custom config:

```bash
scripts/reproduce_final.sh configs/final_study.yaml
```

Pin a custom timestamp or name:

```bash
RUN_STAMP=paper_v1 scripts/reproduce_final.sh
EXPERIMENT_NAME=paper_submission_main scripts/reproduce_final.sh
```

## Archiving old results

Old results were archived under:

```text
output/archive/20260502_222126/
```

For future runs, prefer keeping each experiment timestamped instead of overwriting old directories. If you deliberately reuse a name, you can archive an existing same-name run first:

```bash
ARCHIVE_EXISTING=1 EXPERIMENT_NAME=paper_submission_main scripts/reproduce_final.sh
```

## Better long-term storage/viewing options

For a small paper artifact, the current structure is acceptable if every final run has:

- timestamped output directory
- saved config JSON
- aggregate CSVs
- generated plots
- git commit hash in the paper or manifest

For a larger project, common experiment-tracking practices are:

1. **MLflow**
   - Good for tracking params, metrics, artifacts, and plots in a browsable local web UI.
   - Useful command pattern: log each experiment matrix as one MLflow run and attach CSV/PNG artifacts.

2. **DVC**
   - Good for versioning large generated results outside git while keeping lightweight `.dvc` pointers in the repo.
   - Best if final CSVs/plots become large or need cloud storage.

3. **Weights & Biases / Neptune / Aim**
   - Good interactive dashboards for comparing runs, plots, configs, and artifacts.
   - W&B would work well if you want a polished dashboard for collaborators or screenshots, but it is probably heavier than needed for a deterministic simulator unless you expect many parameter sweeps.
   - If used, log one experiment matrix as a run, with config parameters, aggregate metrics from `comparison_by_clock.csv`, and plots/CSVs as artifacts.

4. **SQLite or DuckDB result database**
   - Good lightweight option for this simulator.
   - Store every row from `comparison_runs.csv` and `comparison_by_clock.csv` in a single queryable database, while keeping plots as artifacts.

Recommended next improvement for this repo:

- Keep timestamped directories for raw artifacts.
- Add a small `results_index.csv` or DuckDB database later if many runs accumulate.
- Do not commit all generated outputs to git; commit only final paper figures/tables or an archived release artifact.
