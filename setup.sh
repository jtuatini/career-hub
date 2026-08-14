#!/usr/bin/env bash
# One-time setup for Application Hub. Safe to re-run any time.
set -euo pipefail
cd "$(cd "$(dirname "$0")" && pwd)"

say()  { printf '\n\033[1m%s\033[0m\n' "$*"; }
fail() { printf '\n\033[31m%s\033[0m\n' "$*"; exit 1; }

# need <cmd> <install hint> [brew formula]
need() {
  local cmd=$1 hint=$2 formula=${3:-}
  command -v "$cmd" >/dev/null 2>&1 && return 0
  if [ -n "$formula" ] && command -v brew >/dev/null 2>&1; then
    read -r -p "$cmd is missing. Install it now with 'brew install $formula'? [Y/n] " ans
    case ${ans:-Y} in
      [Yy]*) brew install "$formula" && return 0 ;;
    esac
  fi
  fail "$cmd is required. Install it with: $hint"
}

say "Checking prerequisites…"
need uv "brew install uv   (or see https://docs.astral.sh/uv/)" uv
need node "brew install node   (or see https://nodejs.org)" node
need npm "npm ships with Node — reinstall Node" ""

if ! command -v pdflatex >/dev/null 2>&1 \
   && [ ! -x "$HOME/Library/TinyTeX/bin/universal-darwin/pdflatex" ]; then
  printf '\nNote: LaTeX (TinyTeX) was not found — PDF compilation will not work until you install it:\n'
  printf '  curl -sL "https://yihui.org/tinytex/install-bin-unix.sh" | sh\n'
fi

say "Installing backend dependencies…"
(cd backend && uv sync)

say "Preparing the database…"
(cd backend && uv run alembic upgrade head)

say "Installing frontend dependencies…"
(cd frontend && npm install)

if [ ! -f backend/.env ]; then
  say "Creating backend/.env (optional settings live here)…"
  cat > backend/.env <<'ENV'
# Optional settings — the app runs fine with this file untouched.
# Metered API fallback (only used when no CLI engine is available):
# ANTHROPIC_API_KEY=sk-ant-...
# Local hiring-agent repo for the optional "Hiring-agent" ATS scan:
# ATS_REPO_PATH=/path/to/hiring-agent
ENV
fi

say "Done. Start the app with: ./start.sh"
