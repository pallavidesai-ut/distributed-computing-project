#!/usr/bin/env bash
set -euo pipefail

CONFIGS=(
  configs/sensitivity_client_count_32.yaml
  configs/sensitivity_client_count_512.yaml
  configs/sensitivity_rf3.yaml
  configs/sensitivity_rf5.yaml
)

if [[ -x ".venv/bin/python" ]]; then
  PYTHON="${PYTHON:-.venv/bin/python}"
else
  PYTHON="${PYTHON:-python}"
fi

resolve_output_dir() {
  local config_path="$1"
  shift
  local base_output_dir
  base_output_dir="$($PYTHON - "$config_path" <<'PY'
import sys
from pathlib import Path
import yaml

config = yaml.safe_load(Path(sys.argv[1]).read_text()) or {}
print(config.get("output-dir", "output/experiments"))
PY
)"
  local candidate="${OUTPUT_DIR:-$base_output_dir}"
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --output-dir)
        if [[ $# -lt 2 ]]; then
          echo "Missing value for --output-dir" >&2
          exit 1
        fi
        candidate="$2"
        shift 2
        ;;
      --output-dir=*)
        candidate="${1#--output-dir=}"
        shift
        ;;
      *)
        shift
        ;;
    esac
  done
  printf '%s\n' "$candidate"
}

RUN_DIRS=()
SUMMARY_ROOT=""
for config in "${CONFIGS[@]}"; do
  echo "=== Running sensitivity config: $config ==="
  output_root="$(resolve_output_dir "$config" "$@")"
  SUMMARY_ROOT="$output_root"
  before=$(mktemp)
  after=$(mktemp)
  find "$output_root" -maxdepth 1 -mindepth 1 -type d 2>/dev/null | sort > "$before" || true
  scripts/reproduce_final.sh "$config" "$@"
  find "$output_root" -maxdepth 1 -mindepth 1 -type d 2>/dev/null | sort > "$after" || true
  new_dir=$(comm -13 "$before" "$after" | tail -1 || true)
  rm -f "$before" "$after"
  if [[ -n "$new_dir" ]]; then
    RUN_DIRS+=("$new_dir")
  fi
done

if [[ ${#RUN_DIRS[@]} -gt 0 ]]; then
  SUMMARY_DIR="$SUMMARY_ROOT/sensitivity_summary_${RUN_STAMP:-$(date +%Y-%m-%d_%H-%M-%S)}"
  scripts/summarize_sensitivity.py "$SUMMARY_DIR" "${RUN_DIRS[@]}"
  echo "=== Sensitivity summary: $SUMMARY_DIR ==="
fi
