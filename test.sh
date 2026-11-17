#!/usr/bin/env bash
# Runs the tests against a Compose stack with the test settings. Always stops the stack.
set -uo pipefail
cd "$(dirname "$0")"

docker info >/dev/null 2>&1 || { echo "ERROR: Docker does not answer. Start Docker and run again." >&2; exit 1; }
[ -f .env ] || { echo "ERROR: .env is missing. Run: cp .env.example .env" >&2; exit 1; }

export COMPOSE_FILE=docker-compose.yml:docker-compose.test.yml
cleanup() { docker compose down >/dev/null 2>&1; }
trap cleanup EXIT

docker compose up -d --build redis api worker || { echo "ERROR: the stack did not start." >&2; exit 1; }

ok=0
for _ in $(seq 1 60); do
  code=$(curl -s -o /dev/null -w '%{http_code}' http://localhost:8000/health || true)
  if [ "$code" = "200" ]; then ok=1; break; fi
  sleep 1
done
[ "$ok" = 1 ] || { echo "ERROR: GET /health did not return 200 within 60 seconds." >&2; exit 1; }

uv run --frozen pytest -q
exit $?
