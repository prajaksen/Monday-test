import json
import os
import subprocess
import time
import uuid
from typing import Any, Dict, Optional
import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, request

load_dotenv()

from telemetry import init_telemetry, traced_span, run_subprocess_traced, set_tenant_context

app = Flask(__name__)
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2")

# Initialize OpenTelemetry (OTLP -> Collector -> Langfuse)
init_telemetry(service_name=os.getenv("OTEL_SERVICE_NAME", "langfuse-poc"), app=app)


def get_postgres_password() -> str:
    """Fetch PostgreSQL password from Kubernetes secret or environment."""
    if os.environ.get("POSTGRES_PASSWORD"):
        return os.environ.get("POSTGRES_PASSWORD", "")

    try:
        result = run_subprocess_traced(
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
    except Exception as exc:
        print(f"Failed to fetch password from Kubernetes: {exc}")

    return ""


def _first_non_empty(*values: Any) -> Optional[str]:
    for value in values:
        if value is None:
            continue
        if isinstance(value, str) and value.strip() == "":
            continue
        return str(value)
    return None


def _serialize_span_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (list, dict)):
        return json.dumps(value, default=str)
    return str(value)


def _set_span_attribute(span: Any, key: str, value: Any) -> None:
    if span is None:
        return
    setter = getattr(span, "set_attribute", None)
    if callable(setter):
        try:
            setter(key, _serialize_span_value(value))
        except Exception:
            pass


def extract_monday_context(payload: Dict[str, Any]) -> Dict[str, str]:
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

    return {
        "tenant_name": tenant_name,
        "user_email": user_email,
        "monday_item_id": _first_non_empty(payload.get("pulseId"), payload.get("itemId"), item_payload.get("id")) or "",
        "monday_board_id": _first_non_empty(payload.get("boardId"), board_payload.get("id"), payload.get("board_id")) or "",
        "status": _first_non_empty(event_payload.get("type"), payload.get("status"), payload.get("eventType")) or "updated",
    }


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


def call_ollama(prompt: str, model: str = OLLAMA_MODEL) -> str:
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
    }

    response = requests.post(f"{OLLAMA_BASE_URL}/api/generate", json=payload, timeout=30)
    response.raise_for_status()
    body = response.json()
    return body.get("response", "")


@app.route("/chat", methods=["POST"])
def chat():
    request_id = str(uuid.uuid4())
    started_at = time.perf_counter()
    message = None
    response_text = ""
    error_message = None

    with traced_span(
        "chat.request",
        request_id,
        {
            "model.name": OLLAMA_MODEL,
            "request.id": request_id,
        },
    ) as root_span:
        try:
            payload = request.get_json(silent=True) or {}
            with traced_span("request.validation", request_id, {"input": payload}) as validation_span:
                message = payload.get("message")
                if not isinstance(message, str) or not message.strip():
                    error_message = "message is required"
                    _set_span_attribute(validation_span, "validation.result", "invalid")
                    _set_span_attribute(validation_span, "errors", error_message)
                    _set_span_attribute(root_span, "errors", error_message)
                    return jsonify({"error": error_message, "request_id": request_id}), 400

                _set_span_attribute(validation_span, "validation.result", "valid")
                _set_span_attribute(validation_span, "user.prompt", message)

            with traced_span("llm.call", request_id, {"model.name": OLLAMA_MODEL, "user.prompt": message}) as llm_span:
                response_text = call_ollama(message)
                _set_span_attribute(llm_span, "response", response_text)

            with traced_span("response.generation", request_id, {"response": response_text}) as generation_span:
                latency_ms = round((time.perf_counter() - started_at) * 1000, 2)
                _set_span_attribute(root_span, "user.prompt", message)
                _set_span_attribute(root_span, "response", response_text)
                _set_span_attribute(root_span, "latency.ms", latency_ms)
                _set_span_attribute(root_span, "model.name", OLLAMA_MODEL)
                _set_span_attribute(root_span, "errors", "")
                _set_span_attribute(generation_span, "latency.ms", latency_ms)
                return jsonify({"response": response_text, "request_id": request_id}), 200
        except Exception as exc:
            error_message = str(exc)
            latency_ms = round((time.perf_counter() - started_at) * 1000, 2)
            _set_span_attribute(root_span, "errors", error_message)
            _set_span_attribute(root_span, "latency.ms", latency_ms)
            _set_span_attribute(root_span, "model.name", OLLAMA_MODEL)
            if hasattr(root_span, "record_exception"):
                try:
                    root_span.record_exception(exc)
                except Exception:
                    pass
            return jsonify({"error": error_message, "request_id": request_id}), 500


@app.route("/monday-webhook", methods=["POST"])
def monday_webhook():
    """Receive a Monday.com-style webhook payload and trigger the SQL update flow."""
    payload = request.get_json(silent=True) or {}

    if WEBHOOK_SECRET:
        provided_secret = request.headers.get("X-Webhook-Secret", "")
        if provided_secret != WEBHOOK_SECRET:
            return jsonify({"status": "forbidden", "error": "invalid webhook secret"}), 403

    context = extract_monday_context(payload)

    print("=" * 60)
    print("Webhook received")
    print(json.dumps(payload, indent=2, default=str))

    env = os.environ.copy()
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
    # propagate tenant context into the current trace and baggage
    try:
        set_tenant_context(context)
    except Exception:
        pass

    result = run_subprocess_traced(["bash", script_path], capture_output=True, text=True, env=env)

    print(result.stdout)
    if result.stderr:
        print(result.stderr)

    response = {
        "status": "success" if result.returncode == 0 else "error",
        "context": context,
        "output": result.stdout,
        "error": result.stderr,
    }

    return jsonify(response)


@app.route("/health", methods=["GET"])
@app.route("/healthz", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


@app.route("/")
def home():
    return "Langfuse AI Chatbot Running"


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
