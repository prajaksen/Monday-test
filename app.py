import json
import os
import subprocess
from typing import Any, Dict, Optional

from flask import Flask, jsonify, request

app = Flask(__name__)
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")


def _first_non_empty(*values: Any) -> Optional[str]:
    for value in values:
        if value is None:
            continue
        if isinstance(value, str) and value.strip() == "":
            continue
        return str(value)
    return None


def extract_monday_context(payload: Dict[str, Any]) -> Dict[str, str]:
    event_payload = payload.get("event") or {}
    board_payload = payload.get("board") or {}
    item_payload = payload.get("item") or {}
    user_payload = payload.get("user") or {}

    tenant_name = _first_non_empty(
        payload.get("tenantName"),
        payload.get("boardName"),
        board_payload.get("name"),
        payload.get("tenant"),
        payload.get("organization"),
    ) or "unknown-tenant"

    user_email = _first_non_empty(
        payload.get("userEmail"),
        payload.get("email"),
        user_payload.get("email"),
    )

    if user_email is None:
        user_name = _first_non_empty(payload.get("userName"), user_payload.get("name"))
        if user_name is not None:
            user_email = f"{user_name.replace(' ', '.').lower()}@unknown.local"
        else:
            user_email = "unknown-user@unknown.local"

    context = {
        "tenant_name": tenant_name,
        "user_email": user_email,
        "monday_item_id": _first_non_empty(payload.get("pulseId"), payload.get("itemId"), item_payload.get("id")) or "",
        "monday_board_id": _first_non_empty(payload.get("boardId"), board_payload.get("id"), payload.get("board_id")) or "",
        "status": _first_non_empty(event_payload.get("type"), payload.get("status"), payload.get("eventType")) or "updated",
    }
    return context


def render_tenant_user_sql(tenant_name: str, user_email: str, monday_item_id: str, monday_board_id: str, status: str) -> str:
    def sql_literal(value: str) -> str:
        if value is None or value == "":
            return "NULL"
        escaped = str(value).replace("'", "''")
        return f"'{escaped}'"

    return f"""
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
VALUES ({sql_literal(tenant_name)}, {sql_literal(user_email)}, {sql_literal(monday_item_id)}, {sql_literal(monday_board_id)}, {sql_literal(status)})
ON CONFLICT (tenant_name, user_email) DO UPDATE
SET monday_item_id = EXCLUDED.monday_item_id,
    monday_board_id = EXCLUDED.monday_board_id,
    status = EXCLUDED.status,
    updated_at = NOW();

SELECT tenant_name, user_email, monday_item_id, monday_board_id, status, updated_at
FROM tenant_user_mapping
WHERE tenant_name = {sql_literal(tenant_name)} AND user_email = {sql_literal(user_email)};
"""


@app.route("/monday-webhook", methods=["POST"])
def monday_webhook():
    """Receive a Monday.com-style webhook payload and trigger the SQL update flow."""
    if WEBHOOK_SECRET:
        provided_secret = request.headers.get("X-Webhook-Secret", "")
        if provided_secret != WEBHOOK_SECRET:
            return jsonify({"status": "forbidden", "error": "invalid webhook secret"}), 403

    payload = request.get_json(silent=True) or {}
    context = extract_monday_context(payload)

    print("=" * 60)
    print("Webhook received")
    print(json.dumps(payload, indent=2, default=str))

    env = os.environ.copy()
    env.update(
        {
            "MONDAY_TENANT_NAME": context["tenant_name"],
            "MONDAY_USER_EMAIL": context["user_email"],
            "MONDAY_ITEM_ID": context["monday_item_id"],
            "MONDAY_BOARD_ID": context["monday_board_id"],
            "MONDAY_STATUS": context["status"],
            "MONDAY_PAYLOAD_JSON": json.dumps(payload, default=str),
        }
    )

    script_path = os.path.join(os.path.dirname(__file__), "scripts", "update-langfuse.sh")
    result = subprocess.run(["bash", script_path], capture_output=True, text=True, env=env)

    print(result.stdout)
    if result.stderr:
        print(result.stderr)

    return jsonify(
        {
            "status": "success" if result.returncode == 0 else "error",
            "context": context,
            "output": result.stdout,
            "error": result.stderr,
        }
    )


@app.route("/healthz", methods=["GET"])
def healthz():
    return jsonify({"status": "ok"})


@app.route("/")
def home():
    return "Langfuse Webhook Running"


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
