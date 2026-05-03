# distributed-computing-project

Per-object causality simulator for comparing `VV`, `DVV`, and `lease-DVV` under churn.

## Entry Points

- `scripts/reproduce_final.sh` — main paper/reproducibility script
- `python run_experiments.py`
- `python simulate.py`
- `python analyze_run.py`

## Full Study Pipeline

Run a single simulation:

```bash
python simulate.py --clock lease_dvv --profile sustained --run-name lease_sustained
```

Analyze one saved run:

```bash
python analyze_run.py --input-dir output/runs --run-name lease_sustained
```

Run the main paper-oriented experiment matrix, including lease-duration ablations, timestamped output directories, aggregate tables, report notes, and figures:

```bash
scripts/reproduce_final.sh
```

Use a custom config or override parameters:

```bash
scripts/reproduce_final.sh configs/final_study.yaml
scripts/reproduce_final.sh configs/final_study.yaml --seeds 1 --sim-time 60
```

The script writes organized results under `output/experiments/<timestamp>_per_object_clock_study_final/`.

Run optional follow-up sweeps for the report:

```bash
scripts/reproduce_sensitivity.sh   # client-count and replication-factor sensitivity
scripts/reproduce_extremes.sh      # stress scenarios that amplify clock differences
```

## Study Notes

- Design summary: [docs/clock_study_design.md](/Users/nelly/Projects/ut_austin/spring_2026/distributed computing/distributed-computing-project/docs/clock_study_design.md)
- Assumptions and open questions: [docs/assumptions_and_questions.md](/Users/nelly/Projects/ut_austin/spring_2026/distributed computing/distributed-computing-project/docs/assumptions_and_questions.md)
- Simulator evolution log: [docs/simulator_evolution.md](/Users/nelly/Projects/ut_austin/spring_2026/distributed computing/distributed-computing-project/docs/simulator_evolution.md)
- Simulator methodology: [docs/simulator_methodology.md](docs/simulator_methodology.md)
- Paper plan: [docs/paper_plan.md](docs/paper_plan.md)
- Results workflow: [docs/results_workflow.md](docs/results_workflow.md)
- Draft report: [docs/report_draft.tex](docs/report_draft.tex)
- Proposal and pivot notes: [docs/DC Project Proposal.md](/Users/nelly/Projects/ut_austin/spring_2026/distributed computing/distributed-computing-project/docs/DC%20Project%20Proposal.md)
