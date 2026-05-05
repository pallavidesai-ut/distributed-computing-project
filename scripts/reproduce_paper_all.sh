#!/usr/bin/env bash
set -euo pipefail

# Runs every experiment set used for the final paper into one timestamped directory.
# Usage: scripts/reproduce_paper_all.sh [extra run_experiments.py args]

STAMP="${RUN_STAMP:-$(date +%Y-%m-%d_%H-%M-%S)}"
PAPER_DIR="${PAPER_DIR:-output/final_paper_${STAMP}}"

mkdir -p "$PAPER_DIR"
export RUN_STAMP="$STAMP"
export OUTPUT_DIR="$PAPER_DIR"

echo "Writing all final-paper experiment outputs under: $PAPER_DIR"

scripts/reproduce_final.sh configs/final_study.yaml --output-dir "$PAPER_DIR" "$@"
scripts/reproduce_sensitivity.sh --output-dir "$PAPER_DIR" "$@"
scripts/reproduce_extremes.sh --output-dir "$PAPER_DIR" "$@"

cat > "$PAPER_DIR/README.txt" <<EOF
Final paper experiment bundle
Generated: $STAMP

Included experiment sets:
- Main final study: configs/final_study.yaml
- Sensitivity studies: configs/sensitivity_client_count_32.yaml, configs/sensitivity_client_count_512.yaml, configs/sensitivity_rf3.yaml, configs/sensitivity_rf5.yaml
- Extreme scenarios: configs/extreme_hotspot_churn.yaml, configs/extreme_sparse_replication.yaml

Each subdirectory contains its own manifest, copied config, aggregate outputs, figures, and per-run artifacts.
EOF

echo "Done. Final-paper bundle: $PAPER_DIR"
