# Results Workflow

## What the experiment runner produces

`run_experiments.py` first generates tabular results and plots. `scripts/reproduce_final.sh` then calls `scripts/organize_experiment.py` to reorganize the completed run into a paper-friendly bundle.

Organized experiment layout:

```text
output/experiments/<timestamp>_<experiment_name>/
  README.md
  manifest.json
  config/
    source_config.yaml
    experiment_config.json
  aggregate/
    comparison_runs.csv
    comparison_by_clock.csv
    lease_duration_ablation.csv
    metadata_reduction_vs_recall_loss.csv
  figures/
    *_vs_profile.png/.pdf
    lease_ablation_*.png/.pdf
    metadata_reduction_vs_recall_loss.png/.pdf
    same_replica_concurrency_example.png/.pdf
  time_series/
    *.csv
    *.png/.pdf
  runs/
    stable_vv_seed1/
      raw/
        *_writes.csv
        *_accuracy.csv
        *_decisions.csv
        ...
      analysis/
        *.csv
        *.png
      *_config.json
      *_summary.json
```

Use `aggregate/`, `figures/`, and `time_series/` for paper-level results. Use `runs/` when inspecting an individual profile/clock/seed run. Aggregate bar/line plots include standard-error bars across seeds when multiple seeds are present. PNG plots are always generated; pass `--write-pdf` to `scripts/reproduce_final.sh` or `run_experiments.py` to also emit PDF copies for LaTeX.

## Timestamped runs

Use the wrapper script:

```bash
scripts/reproduce_final.sh
```

By default this reads `configs/final_study.yaml` and writes to a timestamped experiment name such as:

```text
output/experiments/2026-05-02_22-21-26_per_object_clock_study_final/
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

For a small paper artifact, the organized timestamped directory is acceptable if every final run has:

- timestamped output directory
- saved source and resolved configs under `config/`
- aggregate CSVs under `aggregate/`
- generated plots under `figures/` and `time_series/`
- git commit hash in `manifest.json`

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
