#!/usr/bin/env bash
# Stop Application Hub: shuts down the backend and the web app.
# Safe to run any time — it tells you if nothing was running.
set -uo pipefail
cd "$(cd "$(dirname "$0")" && pwd)"

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }

stopped=0
for port in 8321 5173; do
  pids=$(lsof -nP -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true)
  if [ -n "$pids" ]; then
    kill $pids 2>/dev/null || true
    stopped=1
  fi
done

# Give them a moment to exit cleanly, then force anything that lingered.
sleep 2
for port in 8321 5173; do
  pids=$(lsof -nP -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true)
  [ -n "$pids" ] && kill -9 $pids 2>/dev/null || true
done

if [ "$stopped" = 1 ]; then
  say "Career Hub is stopped. You can close this window."
else
  say "Career Hub wasn't running — nothing to stop. You can close this window."
fi
