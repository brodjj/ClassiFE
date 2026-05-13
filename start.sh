#!/bin/bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

cleanup() {
    echo "Shutting down..."
    kill "$APP_PID" "$CLASSIFIER_PID" "$TASK_PID" 2>/dev/null
    wait
}
trap cleanup INT TERM

echo "Starting classifier model (port 8081)..."
bash ~/bin/classifier.sh &
CLASSIFIER_PID=$!

echo "Waiting for classifier to be ready..."
until curl -sf http://localhost:8081/health > /dev/null 2>&1; do sleep 1; done
echo "Classifier ready. Starting task model (port 8080)..."
bash ~/bin/task.sh &
TASK_PID=$!

echo "Starting ClassiFE (port 5000)..."
source "$SCRIPT_DIR/venv/bin/activate"
python "$SCRIPT_DIR/app.py" &
APP_PID=$!

echo "ClassiFE running at http://localhost:5000 — Ctrl+C to stop all."
wait $APP_PID
