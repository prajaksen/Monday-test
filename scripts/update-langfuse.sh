#!/bin/bash
set -euo pipefail

NAMESPACE="${NAMESPACE:-langfuse}"
POSTGRES_POD="${POSTGRES_POD:-langfuse-postgresql-0}"
POSTGRES_DB="${POSTGRES_DB:-postgres_langfuse}"
POSTGRES_USER="${POSTGRES_USER:-postgres}"
POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-}"

if [[ -z "$POSTGRES_PASSWORD" ]]; then
  echo "POSTGRES_PASSWORD is not set. Export it before running this script." >&2
  exit 1
fi

kubectl exec -i -n "$NAMESPACE" "$POSTGRES_POD" -- \
  env PGPASSWORD="$POSTGRES_PASSWORD" \
  psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" <<'SQL'

-- Example update for a PoC workflow.
UPDATE organizations
SET name = 'Updated from Monday'
WHERE name = 'orolabs-test1';

SELECT id, name FROM organizations;

SQL
