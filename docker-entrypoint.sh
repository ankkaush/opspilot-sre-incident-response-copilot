#!/usr/bin/env bash
set -euo pipefail

echo "[entrypoint] running migrations..."
alembic upgrade head

echo "[entrypoint] seeding synthetic environment (idempotent)..."
python -m opspilot.seed

echo "[entrypoint] starting: $*"
exec "$@"
