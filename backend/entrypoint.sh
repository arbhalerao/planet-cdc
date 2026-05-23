#!/bin/bash
set -e
if [ "${SKIP_MIGRATIONS:-false}" != "true" ]; then
    echo "Running migrations..."
    alembic upgrade head
fi
exec "$@"
