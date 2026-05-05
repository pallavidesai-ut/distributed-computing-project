#!/usr/bin/env bash
set -euo pipefail

# Run the focused experiment matrix for the 5--6 page final report.
#
# Default design:
#   - physical actor domain
#   - stable/low/sustained/burst churn profiles
#   - exact VV, exact DVV, and lease-DVV
#   - lease durations 8/16/32
#   - five seeds
#
# The experiment name is unique by default and includes a timestamp, process id,
# and current git commit when available. Pass any run_experiments.py/reproduce_final
# overrides after the script name, e.g.:
#
#   scripts/reproduce_report_5page.sh --jobs 8 --sim-time 480
#   scripts/reproduce_report_5page.sh --seeds 1 --sim-time 10 --jobs 2 --fixed-lease-duration

CONFIG_PATH="${CONFIG_PATH:-configs/final_study.yaml}"
RUN_STAMP="${RUN_STAMP:-$(date +%Y-%m-%d_%H-%M-%S)}"
RUN_ID="${RUN_ID:-${RUN_STAMP}_pid$$}"
GIT_SHA="$(git rev-parse --short HEAD 2>/dev/null || true)"
if [[ -n "$GIT_SHA" ]]; then
  RUN_ID="${RUN_ID}_${GIT_SHA}"
fi

OUTPUT_ROOT="${OUTPUT_DIR:-output/experiments}"
EXPERIMENT_NAME="${EXPERIMENT_NAME:-report_5page_${RUN_ID}}"
JOBS="${JOBS:-4}"
SIM_TIME="${SIM_TIME:-240}"

export RUN_STAMP
export OUTPUT_DIR="$OUTPUT_ROOT"
export EXPERIMENT_NAME

echo "Running focused 5-page report experiment"
echo "  config: $CONFIG_PATH"
echo "  experiment: $EXPERIMENT_NAME"
echo "  output root: $OUTPUT_ROOT"
echo "  sim time: $SIM_TIME"
echo "  jobs: $JOBS"

scripts/reproduce_final.sh "$CONFIG_PATH" \
  --profiles stable low sustained burst \
  --clocks vv dvv lease_dvv \
  --actor-domain physical \
  --seeds 1 2 3 4 5 \
  --sim-time "$SIM_TIME" \
  --lease-durations 8 16 32 \
  --jobs "$JOBS" \
  --progress \
  --write-pdf \
  "$@"

EXPERIMENT_DIR="$OUTPUT_ROOT/$EXPERIMENT_NAME"
SELECTED_DIR="$EXPERIMENT_DIR/selected_report_artifacts"
mkdir -p "$SELECTED_DIR"

# Collect the artifacts most likely to be included in the short report.
copy_if_exists() {
  local path="$1"
  if [[ -e "$path" ]]; then
    cp -f "$path" "$SELECTED_DIR/"
  fi
}

for artifact in \
  "$EXPERIMENT_DIR/aggregate/headline_results.csv" \
  "$EXPERIMENT_DIR/aggregate/comparison_by_clock.csv" \
  "$EXPERIMENT_DIR/aggregate/lease_duration_ablation.csv" \
  "$EXPERIMENT_DIR/aggregate/metadata_reduction_vs_recall_loss.csv" \
  "$EXPERIMENT_DIR/figures/metadata_bytes_vs_profile.png" \
  "$EXPERIMENT_DIR/figures/metadata_bytes_vs_profile.pdf" \
  "$EXPERIMENT_DIR/figures/metadata_reduction_vs_recall_loss.png" \
  "$EXPERIMENT_DIR/figures/metadata_reduction_vs_recall_loss.pdf" \
  "$EXPERIMENT_DIR/figures/stale_siblings_vs_profile.png" \
  "$EXPERIMENT_DIR/figures/stale_siblings_vs_profile.pdf" \
  "$EXPERIMENT_DIR/figures/lease_ablation_recall.png" \
  "$EXPERIMENT_DIR/figures/lease_ablation_recall.pdf" \
  "$EXPERIMENT_DIR/time_series/metadata_bytes_over_time_report.png" \
  "$EXPERIMENT_DIR/time_series/metadata_bytes_over_time_report.pdf"; do
  copy_if_exists "$artifact"
done

cat > "$EXPERIMENT_DIR/report_artifacts_README.md" <<EOF
# 5-page report artifacts

Experiment: $EXPERIMENT_NAME
Config: $CONFIG_PATH
Output: $EXPERIMENT_DIR

Recommended report inputs copied to \`selected_report_artifacts/\`:

- \`headline_results.csv\`
- \`comparison_by_clock.csv\`
- \`lease_duration_ablation.csv\`
- \`metadata_reduction_vs_recall_loss.csv\`
- \`metadata_bytes_vs_profile.png/.pdf\`
- \`metadata_reduction_vs_recall_loss.png/.pdf\`
- \`stale_siblings_vs_profile.png/.pdf\`
- \`lease_ablation_recall.png/.pdf\`
- \`metadata_bytes_over_time_report.png/.pdf\` when generated
EOF

echo "Done."
echo "  experiment dir: $EXPERIMENT_DIR"
echo "  selected artifacts: $SELECTED_DIR"
