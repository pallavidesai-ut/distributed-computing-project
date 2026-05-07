#!/usr/bin/env bash
set -euo pipefail

# Runs every experiment set used for the final paper into one timestamped directory.
# Usage: scripts/reproduce_paper_all.sh [extra run_experiments.py args]

STAMP="${RUN_STAMP:-$(date +%Y-%m-%d_%H-%M-%S)}"
PAPER_DIR="${PAPER_DIR:-output/final_paper_${STAMP}}"

mkdir -p "$PAPER_DIR"
export RUN_STAMP="$STAMP"
export OUTPUT_DIR="$PAPER_DIR"

if [[ -x ".venv/bin/python" ]]; then
  PYTHON="${PYTHON:-.venv/bin/python}"
else
  PYTHON="${PYTHON:-python}"
fi

echo "Writing all final-paper experiment outputs under: $PAPER_DIR"

"$PYTHON" scripts/run_dvv_advantage_experiment.py \
  --output-dir "$PAPER_DIR" \
  --experiment-name fanout_dvv_vs_vv
scripts/reproduce_final.sh configs/final_study.yaml --output-dir "$PAPER_DIR" "$@"

cat > "$PAPER_DIR/README.txt" <<EOF
Final paper experiment bundle
Generated: $STAMP

Included experiment sets:
- Deterministic DVV shared-context fanout comparison: scripts/run_dvv_advantage_experiment.py
- Main fixed-lease churn study: configs/final_study.yaml

Each subdirectory contains its own manifest, copied config, aggregate outputs, figures, and per-run artifacts.
EOF

echo "Done. Final-paper bundle: $PAPER_DIR"
