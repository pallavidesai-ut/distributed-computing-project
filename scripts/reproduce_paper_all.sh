#!/usr/bin/env bash
set -euo pipefail

# Runs the three primary paper experiment themes into one timestamped directory.
# Usage: scripts/reproduce_paper_all.sh [extra run_experiments.py args]
#
# Extra args are forwarded to the stochastic run_experiments.py matrices. The
# deterministic fanout comparison only consumes --write-pdf from those args.

STAMP="${RUN_STAMP:-$(date +%Y-%m-%d_%H-%M-%S)}"
PAPER_DIR="${PAPER_DIR:-output/final_paper_${STAMP}}"

if [[ -x ".venv/bin/python" ]]; then
  PYTHON="${PYTHON:-.venv/bin/python}"
else
  PYTHON="${PYTHON:-python}"
fi

FANOUT_ARGS=()
for arg in "$@"; do
  if [[ "$arg" == "--write-pdf" ]]; then
    FANOUT_ARGS+=(--write-pdf)
  fi
done

mkdir -p "$PAPER_DIR"
export RUN_STAMP="$STAMP"
export OUTPUT_DIR="$PAPER_DIR"

echo "Writing all final-paper experiment outputs under: $PAPER_DIR"

echo "=== Theme 1: DVV shared-context fanout advantage vs VV ==="
if [[ ${#FANOUT_ARGS[@]} -gt 0 ]]; then
  "$PYTHON" scripts/run_dvv_advantage_experiment.py \
    --output-dir "$PAPER_DIR" \
    --run-stamp "$STAMP" \
    --experiment-name fanout_dvv_vs_vv \
    "${FANOUT_ARGS[@]}"
else
  "$PYTHON" scripts/run_dvv_advantage_experiment.py \
    --output-dir "$PAPER_DIR" \
    --run-stamp "$STAMP" \
    --experiment-name fanout_dvv_vs_vv
fi

echo "=== Theme 2: final churn study, VV vs DVV vs fixed lease-DVV ==="
EXPERIMENT_NAME="${STAMP}_final_vv_dvv_lease" \
  scripts/reproduce_final.sh configs/final_study.yaml \
    --output-dir "$PAPER_DIR" \
    --clocks vv dvv lease_dvv \
    "$@"

echo "=== Theme 3: lease tradeoff, exact DVV vs fixed and adaptive lease-DVV ==="
EXPERIMENT_NAME="${STAMP}_lease_dvv_adaptive" \
  scripts/reproduce_final.sh configs/final_study.yaml \
    --output-dir "$PAPER_DIR" \
    --clocks dvv lease_dvv adaptive_lease_dvv \
    "$@"

cat > "$PAPER_DIR/README.txt" <<EOF
Final paper experiment bundle
Generated: $STAMP

Included experiment sets:
- Theme 1, fanout DVV vs VV: ${STAMP}_fanout_dvv_vs_vv deterministic shared-context metadata comparison
- Theme 2, final churn study: configs/final_study.yaml with clocks vv, dvv, lease_dvv
- Theme 3, adaptive lease tradeoff: configs/final_study.yaml with clocks dvv, lease_dvv, adaptive_lease_dvv

Each experiment directory contains its own manifest, config snapshot, aggregate outputs, and figures. The stochastic matrix runs also contain per-run artifacts.
EOF

echo "Done. Final-paper bundle: $PAPER_DIR"
