#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
case "${1:-app}" in
  setup)
    python3 -m venv .venv
    .venv/bin/python -m pip install -r requirements.txt
    if [[ ! -f .env ]]; then cp .env.example .env; chmod 600 .env; fi
    ;;
  test) exec .venv/bin/python -m pytest ;;
  verify) exec .venv/bin/python scripts/verify_live.py ;;
  app) exec .venv/bin/python -m uvicorn server:app --host 127.0.0.1 --port 8502 --log-level warning ;;
  *) echo "Usage: ./run.sh [setup|test|verify|app]"; exit 2 ;;
esac
