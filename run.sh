#!/usr/bin/env bash
# Starts both the backend (FastAPI) and frontend (Vite) dev servers together.
# Ctrl+C stops both.
set -e
cd "$(dirname "$0")"

cleanup() {
  echo
  echo "Stopping..."
  kill "$BACKEND_PID" "$FRONTEND_PID" 2>/dev/null
  wait 2>/dev/null
}
trap cleanup EXIT INT TERM

echo "Starting backend on http://localhost:8000 ..."
(cd backend && .venv/bin/uvicorn app.main:app --reload --port 8000) &
BACKEND_PID=$!

echo "Starting frontend on http://localhost:5173 ..."
(cd frontend && npm run dev) &
FRONTEND_PID=$!

wait
