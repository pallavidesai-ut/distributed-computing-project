# distributed-computing-project

Per-object causality simulator for comparing `VV`, `DVV`, and `lease-DVV` under churn.

## Entry Points

- `python run_experiments.py`
- `python code.py`
- `python analyze_run.py`

## Full Study Pipeline

Run a single simulation:

```bash
python code.py --clock lease_dvv --profile sustained --run-name lease_sustained
```

Analyze one saved run:

```bash
python analyze_run.py --input-dir output/runs --run-name lease_sustained
```

Run the report-oriented experiment matrix, including lease-duration ablations:

```bash
python run_experiments.py
```

The full matrix writes aggregate tables, generated report notes, and figures under `output/experiments/per_object_clock_study/`.

## Study Notes

- Design summary: [docs/clock_study_design.md](/Users/nelly/Projects/ut_austin/spring_2026/distributed computing/distributed-computing-project/docs/clock_study_design.md)
- Assumptions and open questions: [docs/assumptions_and_questions.md](/Users/nelly/Projects/ut_austin/spring_2026/distributed computing/distributed-computing-project/docs/assumptions_and_questions.md)
- Simulator evolution log: [docs/simulator_evolution.md](/Users/nelly/Projects/ut_austin/spring_2026/distributed computing/distributed-computing-project/docs/simulator_evolution.md)
- Draft report: [docs/report_draft.md](/Users/nelly/Projects/ut_austin/spring_2026/distributed computing/distributed-computing-project/docs/report_draft.md)
- Proposal and pivot notes: [docs/DC Project Proposal.md](/Users/nelly/Projects/ut_austin/spring_2026/distributed computing/distributed-computing-project/docs/DC%20Project%20Proposal.md)
