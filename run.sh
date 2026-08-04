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

# Open the app in the default browser once the frontend is actually ready, instead of
# right away (it isn't listening yet at this point).
(
  for _ in $(seq 1 60); do
    if curl -s -o /dev/null http://localhost:5173; then
      if command -v open >/dev/null; then
        open http://localhost:5173
      elif command -v xdg-open >/dev/null; then
        xdg-open http://localhost:5173
      fi
      break
    fi
    sleep 0.5
  done
) &

wait
