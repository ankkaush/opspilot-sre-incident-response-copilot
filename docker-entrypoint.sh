#!/usr/bin/env bash
set -euo pipefail

echo "[entrypoint] running migrations..."
alembic upgrade head

echo "[entrypoint] seeding synthetic environment (idempotent)..."
python -m opspilot.seed

echo "[entrypoint] setting up durable checkpoint tables (idempotent)..."
python -c "from opspilot.agent.checkpointer import get_postgres_checkpointer; get_postgres_checkpointer()"

echo "[entrypoint] starting: $*"
exec "$@"
