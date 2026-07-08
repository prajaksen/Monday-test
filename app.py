import json
import os
import subprocess
from typing import Any, Dict, Optional

from flask import Flask, jsonify, request
from langfuse import Langfuse

app = Flask(__name__)
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")

# Initialize Langfuse for observability
langfuse = Langfuse(
    public_key=os.getenv("LANGFUSE_PUBLIC_KEY", ""),
    secret_key=os.getenv("LANGFUSE_SECRET_KEY", ""),
    host=os.getenv("LANGFUSE_HOST", "http://localhost:3000")
)


def get_postgres_password() -> str:
    """Fetch PostgreSQL password from Kubernetes secret or environment."""
    # Try environment first
    if os.environ.get("POSTGRES_PASSWORD"):
        return os.environ.get("POSTGRES_PASSWORD", "")
    
    # Try to fetch from Kubernetes
    try:
        result = subprocess.run(
            [
                "kubectl",
                "get",
                "secret",
                "-n",
                "langfuse",
                "langfuse-postgresql",
                "-o",
                "jsonpath={.data.password}",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout:
            import base64
            return base64.b64decode(result.stdout).decode("utf-8")
    except Exception as e:
        print(f"Failed to fetch password from Kubernetes: {e}")
    
    return ""


def _first_non_empty(*values: Any) -> Optional[str]:
    for value in values:
        if value is None:
            continue
        if isinstance(value, str) and value.strip() == "":
            continue
        return str(value)
    return None


def extract_monday_context(payload: Dict[str, Any]) -> Dict[str, str]:
    # Handle both dict and string values for event
    event_payload = payload.get("event")
    if not isinstance(event_payload, dict):
        event_payload = {}
    
    board_payload = payload.get("board")
    if not isinstance(board_payload, dict):
        board_payload = {}
    
    item_payload = payload.get("item")
    if not isinstance(item_payload, dict):
        item_payload = {}
    
    user_payload = payload.get("user")
    if not isinstance(user_payload, dict):
        user_payload = {}

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
    # Start a trace in Langfuse
    trace_name = "monday-webhook"
    payload = request.get_json(silent=True) or {}
    
    # Log webhook receipt to Langfuse
    trace_context = {
        "source": "monday",
        "timestamp": __import__("datetime").datetime.utcnow().isoformat(),
    }
    
    if WEBHOOK_SECRET:
        provided_secret = request.headers.get("X-Webhook-Secret", "")
        if provided_secret != WEBHOOK_SECRET:
            langfuse.log_event(
                name="webhook-auth-failed",
                event_name="auth_check",
                input={"secret_provided": bool(provided_secret)},
            )
            return jsonify({"status": "forbidden", "error": "invalid webhook secret"}), 403
        
        langfuse.create_event(
            name="webhook-auth-success",
            input={"secret_provided": bool(provided_secret)},
        )

    context = extract_monday_context(payload)

    print("=" * 60)
    print("Webhook received")
    print(json.dumps(payload, indent=2, default=str))
    
    # Log context extraction
    langfuse.create_event(
        name="context-extracted",
        input=json.dumps(context),
    )

    env = os.environ.copy()
    
    # Explicitly fetch and set POSTGRES_PASSWORD
    postgres_password = get_postgres_password()
    
    env.update(
        {
            "MONDAY_TENANT_NAME": context["tenant_name"],
            "MONDAY_USER_EMAIL": context["user_email"],
            "MONDAY_ITEM_ID": context["monday_item_id"],
            "MONDAY_BOARD_ID": context["monday_board_id"],
            "MONDAY_STATUS": context["status"],
            "MONDAY_PAYLOAD_JSON": json.dumps(payload, default=str),
            "NAMESPACE": env.get("NAMESPACE", "langfuse"),
            "POSTGRES_POD": env.get("POSTGRES_POD", "langfuse-postgresql-0"),
            "POSTGRES_DB": env.get("POSTGRES_DB", "postgres_langfuse"),
            "POSTGRES_USER": env.get("POSTGRES_USER", "postgres"),
            "POSTGRES_PASSWORD": postgres_password,
        }
    )

    script_path = os.path.join(os.path.dirname(__file__), "scripts", "update-langfuse.sh")
    
    result = subprocess.run(["bash", script_path], capture_output=True, text=True, env=env)

    print(result.stdout)
    if result.stderr:
        print(result.stderr)
    
    # Log the SQL execution to Langfuse
    langfuse.create_event(
        name="sql-execution",
        input=json.dumps({
            "tenant_name": context["tenant_name"],
            "user_email": context["user_email"],
            "command": "update-langfuse.sh",
        }),
    )
    
    response = {
        "status": "success" if result.returncode == 0 else "error",
        "context": context,
        "output": result.stdout,
        "error": result.stderr,
    }
    
    # Log final webhook response
    langfuse.create_event(
        name="webhook-completed",
        input=json.dumps({"status": response.get("status"), "context": response.get("context")}),
    )
    
    return jsonify(response)


@app.route("/healthz", methods=["GET"])
def healthz():
    return jsonify({"status": "ok"})


@app.route("/")
def home():
    return "Langfuse Webhook Running"


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001)
