#!/usr/bin/env bash
set -euo pipefail

CONFIGS=(
  configs/extreme_hotspot_churn.yaml
  configs/extreme_sparse_replication.yaml
)

for config in "${CONFIGS[@]}"; do
  echo "=== Running extreme scenario config: $config ==="
  scripts/reproduce_final.sh "$config" "$@"
done
