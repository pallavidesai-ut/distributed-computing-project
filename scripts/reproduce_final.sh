#!/usr/bin/env bash
set -euo pipefail

CONFIG_PATH="${1:-configs/final_study.yaml}"
if [[ $# -gt 0 ]]; then
  shift
fi
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
BASE_OUTPUT_DIR="$($PYTHON - "$CONFIG_PATH" <<'PY'
import sys
from pathlib import Path
import yaml

config = yaml.safe_load(Path(sys.argv[1]).read_text()) or {}
print(config.get("output-dir", "output/experiments"))
PY
)"

resolve_output_dir() {
  local default_dir="$1"
  shift
  local candidate="${OUTPUT_DIR:-$default_dir}"
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

OUTPUT_DIR_ROOT="$(resolve_output_dir "$BASE_OUTPUT_DIR" "$@")"
EXPERIMENT_NAME="${EXPERIMENT_NAME:-${RUN_STAMP}_${BASE_EXPERIMENT_NAME}}"

# Optional one-off archival of an existing same-name run before regeneration:
#   ARCHIVE_EXISTING=1 scripts/reproduce_final.sh configs/final_study.yaml
if [[ "${ARCHIVE_EXISTING:-0}" == "1" ]]; then
  ARCHIVE_DIR="output/archive/${RUN_STAMP}"
  mkdir -p "$ARCHIVE_DIR"
  if [[ -d "$OUTPUT_DIR_ROOT/$EXPERIMENT_NAME" ]]; then
    mv "$OUTPUT_DIR_ROOT/$EXPERIMENT_NAME" "$ARCHIVE_DIR/"
    echo "Archived existing run to $ARCHIVE_DIR/$EXPERIMENT_NAME"
  fi
fi

OUTPUT_PATH="$(pwd)/$OUTPUT_DIR_ROOT/$EXPERIMENT_NAME"

uri_for_path() {
  "$PYTHON" - "$1" <<'PY'
import pathlib
import urllib.parse
import sys

path = pathlib.Path(sys.argv[1]).resolve()
print(urllib.parse.quote(str(path), safe="/"))
PY
}

show_path() {
  local label="$1"
  local path="$2"
  local uri
  uri="$(uri_for_path "$path")"
  echo "  $label: [\"${path}\"](file://$uri)"
}

echo "Running final experiment matrix"
echo "  config: $CONFIG_PATH"
echo "  experiment: $EXPERIMENT_NAME"
show_path "output" "$OUTPUT_PATH"
if [[ $# -gt 0 ]]; then
  "$PYTHON" run_experiments.py --config "$CONFIG_PATH" "$@" --experiment-name "$EXPERIMENT_NAME"
else
  "$PYTHON" run_experiments.py --config "$CONFIG_PATH" --experiment-name "$EXPERIMENT_NAME"
fi

mkdir -p "$OUTPUT_PATH"
cp "$CONFIG_PATH" "$OUTPUT_PATH/source_config.yaml"

if [[ "${SKIP_ORGANIZE:-0}" != "1" ]]; then
  "$PYTHON" scripts/organize_experiment.py "$OUTPUT_PATH"
  show_path "organized" "$OUTPUT_PATH"
  show_path "report" "$OUTPUT_PATH/study_report.md"
  show_path "time series" "$OUTPUT_PATH/time_series"
else
  show_path "report" "$OUTPUT_PATH/study_report.md"
  show_path "time series" "$OUTPUT_PATH/time_series_report"
fi

if [[ -f "$OUTPUT_PATH/manifest.json" ]]; then
  show_path "manifest" "$OUTPUT_PATH/manifest.json"
fi
