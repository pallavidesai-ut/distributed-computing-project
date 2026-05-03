#!/usr/bin/env bash
set -euo pipefail

CONFIGS=(
  configs/sensitivity_client_count_32.yaml
  configs/sensitivity_client_count_512.yaml
  configs/sensitivity_rf3.yaml
  configs/sensitivity_rf5.yaml
)

RUN_DIRS=()
for config in "${CONFIGS[@]}"; do
  echo "=== Running sensitivity config: $config ==="
  before=$(mktemp)
  after=$(mktemp)
  find output/experiments -maxdepth 1 -mindepth 1 -type d 2>/dev/null | sort > "$before" || true
  scripts/reproduce_final.sh "$config" "$@"
  find output/experiments -maxdepth 1 -mindepth 1 -type d 2>/dev/null | sort > "$after" || true
  new_dir=$(comm -13 "$before" "$after" | tail -1 || true)
  rm -f "$before" "$after"
  if [[ -n "$new_dir" ]]; then
    RUN_DIRS+=("$new_dir")
  fi
done

if [[ ${#RUN_DIRS[@]} -gt 0 ]]; then
  SUMMARY_DIR="output/experiments/sensitivity_summary_${RUN_STAMP:-$(date +%Y-%m-%d_%H-%M-%S)}"
  scripts/summarize_sensitivity.py "$SUMMARY_DIR" "${RUN_DIRS[@]}"
  echo "=== Sensitivity summary: $SUMMARY_DIR ==="
fi
