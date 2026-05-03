#!/usr/bin/env bash
set -euo pipefail

CONFIG_PATH="${1:-configs/final_study.yaml}"
if [[ $# -gt 0 ]]; then
  shift
fi
EXTRA_ARGS=("$@")
RUN_STAMP="${RUN_STAMP:-$(date +%Y-%m-%d_%H-%M-%S)}"

if [[ ! -f "$CONFIG_PATH" ]]; then
  echo "Config file not found: $CONFIG_PATH" >&2
  echo "Usage: $0 [path/to/config.yaml]" >&2
  exit 1
fi

if [[ -x ".venv/bin/python" ]]; then
  PYTHON=".venv/bin/python"
else
  PYTHON="${PYTHON:-python}"
fi

BASE_EXPERIMENT_NAME="$($PYTHON - "$CONFIG_PATH" <<'PY'
import sys
from pathlib import Path
import yaml

config = yaml.safe_load(Path(sys.argv[1]).read_text()) or {}
print(config.get("experiment-name", "per_object_clock_study_final"))
PY
)"
EXPERIMENT_NAME="${EXPERIMENT_NAME:-${BASE_EXPERIMENT_NAME}_${RUN_STAMP}}"

# Optional one-off archival of an existing same-name run before regeneration:
#   ARCHIVE_EXISTING=1 scripts/reproduce_final.sh configs/final_study.yaml
if [[ "${ARCHIVE_EXISTING:-0}" == "1" ]]; then
  ARCHIVE_DIR="output/archive/${RUN_STAMP}"
  mkdir -p "$ARCHIVE_DIR"
  if [[ -d "output/experiments/$EXPERIMENT_NAME" ]]; then
    mv "output/experiments/$EXPERIMENT_NAME" "$ARCHIVE_DIR/"
    echo "Archived existing run to $ARCHIVE_DIR/$EXPERIMENT_NAME"
  fi
fi

echo "Running final experiment matrix"
echo "  config: $CONFIG_PATH"
echo "  experiment: $EXPERIMENT_NAME"
"$PYTHON" run_experiments.py --config "$CONFIG_PATH" --experiment-name "$EXPERIMENT_NAME" "${EXTRA_ARGS[@]}"
