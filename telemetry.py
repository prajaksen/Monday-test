import os
import json
import requests
import base64
from contextlib import contextmanager
from typing import Any, Dict, Optional

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter


def _serialize_span_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (list, dict)):
        return json.dumps(value, default=str)
    return str(value)


def _set_span_attribute(span, key: str, value: Any) -> None:
    if span is None:
        return
    try:
        span.set_attribute(key, _serialize_span_value(value))
    except Exception:
        pass


def init_telemetry(service_name: str = "langfuse-poc"):
    """Initialize OpenTelemetry with an OTLP HTTP exporter pointed at Langfuse.

    Environment variables:
      LANGFUSE_OTLP_ENDPOINT - full OTLP HTTP endpoint (defaults to http://localhost:3000/api/public/otel/v1/traces)
            LANGFUSE_API_KEY - optional Bearer token to send in Authorization header
            LANGFUSE_SECRET_KEY - optional explicit secret key (sk-...)
            LANGFUSE_PUBLIC_KEY - optional public key (pk-...) to send as X-Langfuse-Public-Key
    """
    # Priority: explicit OTLP endpoint > LANGFUSE_BASE_URL > LANGFUSE_HOST > default
    base = os.getenv("LANGFUSE_OTLP_ENDPOINT") or os.getenv("LANGFUSE_BASE_URL") or os.getenv(
        "LANGFUSE_HOST", "http://localhost:3000"
    )
    endpoint = base.rstrip("/") + "/api/public/otel/v1/traces"
    # Langfuse OTLP export requires the secret key, not the public key.
    public_key = os.getenv("LANGFUSE_PUBLIC_KEY") or os.getenv("LANGFUSE_PUBLIC") or os.getenv("LANGFUSE_PUB_KEY")
    secret_key = os.getenv("LANGFUSE_SECRET_KEY") or os.getenv("LANGFUSE_API_KEY")
    headers = {}
    if public_key and secret_key:
        auth = base64.b64encode(f"{public_key}:{secret_key}".encode("utf-8")).decode("utf-8")
        headers["Authorization"] = f"Basic {auth}"
        headers["x-langfuse-ingestion-version"] = "4"

    print("[telemetry] OTLP endpoint:", endpoint)
    print("[telemetry] auth header set:", bool(headers.get("Authorization")))
    # Print which headers will be sent, mask the values for safety
    def _mask(v: Optional[str]) -> Optional[str]:
        if not v:
            return None
        if len(v) <= 12:
            return v[:4] + "..."
        return v[:8] + "..." + v[-4:]

    masked = {k: _mask(v) for k, v in headers.items()}
    print("[telemetry] outbound headers:", json.dumps(masked))
    print("[telemetry] service name:", service_name)

    resource = Resource.create({"service.name": service_name})
    provider = TracerProvider(resource=resource)

    # Direct HTTP test to verify headers arrive unchanged at Langfuse
    print("\n=== Direct HTTP Test ===")
    try:
        resp = requests.post(endpoint, headers=headers, json={})
        print("Status:", resp.status_code)
        print("Body:", resp.text)
    except Exception as e:
        print("Direct HTTP Test error:", str(e))
    print("========================\n")

    exporter = OTLPSpanExporter(endpoint=endpoint, headers=headers)
    processor = BatchSpanProcessor(exporter)
    provider.add_span_processor(processor)
    trace.set_tracer_provider(provider)


@contextmanager
def traced_span(name: str, request_id: str, metadata: Optional[Dict[str, Any]] = None):
    tracer = trace.get_tracer(__name__)
    with tracer.start_as_current_span(name) as span:
        _set_span_attribute(span, "request.id", request_id)
        if metadata:
            for key, value in metadata.items():
                _set_span_attribute(span, key, value)
        yield span
