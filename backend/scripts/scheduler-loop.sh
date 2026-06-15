#!/bin/sh
set -e

PYTHON="/usr/local/bin/python"
INTERVAL="${PIPELINE_INTERVAL_SECONDS:-7200}"

echo "Scheduler loop started (interval=${INTERVAL}s)"

while true; do
  if "$PYTHON" -m app.scheduler_ctl should-run; then
    echo "Running scheduled pipeline for all agents"
    "$PYTHON" -m app.pipeline run-all || echo "Pipeline run-all failed, will retry after interval"
  else
    echo "Scheduler paused — skipping pipeline run"
  fi
  echo "Sleeping ${INTERVAL}s until next run"
  sleep "$INTERVAL"
done
