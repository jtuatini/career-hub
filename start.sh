#!/usr/bin/env bash
# Start Application Hub (backend + frontend), open the browser.
# Ctrl-C in this terminal stops both servers.
set -euo pipefail
cd "$(cd "$(dirname "$0")" && pwd)"

[ -d backend/.venv ] || { echo "Run ./setup.sh first."; exit 1; }

(cd backend && exec uv run uvicorn app.main:app --host 127.0.0.1 --port 8321) &
BACKEND_PID=$!
(cd frontend && exec npm run dev) &
FRONTEND_PID=$!
trap 'kill "$BACKEND_PID" "$FRONTEND_PID" 2>/dev/null; wait' INT TERM EXIT

printf 'Waiting for the backend'
for _ in $(seq 1 60); do
  curl -fsS http://127.0.0.1:8321/api/health >/dev/null 2>&1 && break
  printf '.'
  sleep 1
done
printf '\n'

URL="http://localhost:5173"
case "$(uname)" in
  Darwin) open "$URL" ;;
  Linux) xdg-open "$URL" >/dev/null 2>&1 || true ;;
esac
echo "Application Hub running at $URL — press Ctrl-C here to stop."
wait
