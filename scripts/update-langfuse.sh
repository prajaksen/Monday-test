#!/bin/bash
set -euo pipefail

NAMESPACE="${NAMESPACE:-langfuse}"
POSTGRES_POD="${POSTGRES_POD:-langfuse-postgresql-0}"
POSTGRES_DB="${POSTGRES_DB:-postgres_langfuse}"
POSTGRES_USER="${POSTGRES_USER:-postgres}"
POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-}"
TENANT_NAME="${MONDAY_TENANT_NAME:-}"
USER_EMAIL="${MONDAY_USER_EMAIL:-}"
ITEM_ID="${MONDAY_ITEM_ID:-}"
BOARD_ID="${MONDAY_BOARD_ID:-}"
STATUS="${MONDAY_STATUS:-}"
SQL_FILE="${SQL_FILE:-/tmp/langfuse-poc.sql}"

log() {
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $*"
}

if [[ -z "$TENANT_NAME" || -z "$USER_EMAIL" ]]; then
  log "No Monday tenant/user context was supplied; skipping SQL execution."
  exit 0
fi

python3 - "$TENANT_NAME" "$USER_EMAIL" "$ITEM_ID" "$BOARD_ID" "$STATUS" > "$SQL_FILE" <<'PY'
import sys


def sql_literal(value: str):
    if not value:
        return "NULL"
    escaped = str(value).replace("'", "''")
    return f"'{escaped}'"


tenant_name = sys.argv[1]
user_email = sys.argv[2]
item_id = sys.argv[3]
board_id = sys.argv[4]
status = sys.argv[5]

sql = f"""
CREATE TABLE IF NOT EXISTS tenant_user_mapping (
    tenant_name TEXT NOT NULL,
    user_email TEXT NOT NULL,
    monday_item_id TEXT,
    monday_board_id TEXT,
    status TEXT,
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (tenant_name, user_email)
);

INSERT INTO tenant_user_mapping (tenant_name, user_email, monday_item_id, monday_board_id, status)
VALUES ({sql_literal(tenant_name)}, {sql_literal(user_email)}, {sql_literal(item_id)}, {sql_literal(board_id)}, {sql_literal(status)})
ON CONFLICT (tenant_name, user_email) DO UPDATE
SET monday_item_id = EXCLUDED.monday_item_id,
    monday_board_id = EXCLUDED.monday_board_id,
    status = EXCLUDED.status,
    updated_at = NOW();

SELECT tenant_name, user_email, monday_item_id, monday_board_id, status, updated_at
FROM tenant_user_mapping
WHERE tenant_name = {sql_literal(tenant_name)} AND user_email = {sql_literal(user_email)};
"""
print(sql)
PY

if [[ -z "$POSTGRES_PASSWORD" ]]; then
  log "POSTGRES_PASSWORD is not set; using dry-run output only."
  cat "$SQL_FILE"
  exit 0
fi

if ! command -v kubectl >/dev/null 2>&1; then
  log "kubectl is not available; using dry-run output only."
  cat "$SQL_FILE"
  exit 0
fi

if ! kubectl get namespace "$NAMESPACE" >/dev/null 2>&1; then
  log "Namespace $NAMESPACE is not reachable; using dry-run output only."
  cat "$SQL_FILE"
  exit 0
fi

log "Executing tenant mapping SQL inside PostgreSQL pod $POSTGRES_POD"
kubectl exec -i -n "$NAMESPACE" "$POSTGRES_POD" -- \
  env PGPASSWORD="$POSTGRES_PASSWORD" \
  psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" < "$SQL_FILE"
