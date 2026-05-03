#!/usr/bin/env bash
set -euo pipefail

CONFIGS=(
  configs/sensitivity_client_count_32.yaml
  configs/sensitivity_client_count_512.yaml
  configs/sensitivity_rf3.yaml
  configs/sensitivity_rf5.yaml
)

for config in "${CONFIGS[@]}"; do
  echo "=== Running sensitivity config: $config ==="
  scripts/reproduce_final.sh "$config" "$@"
done
