#!/bin/sh
set -e

PYTHON="/usr/local/bin/python"

if [ "$PRELOAD_MODELS" = "true" ]; then
  echo "Preloading models for weekly scheduler"
  "$PYTHON" -c "from app.ml.model_registry import get_model_registry; get_model_registry().preload()"
fi

echo "Starting weekly scheduler loop"
exec "$PYTHON" -m app.weekly.scheduler_loop
