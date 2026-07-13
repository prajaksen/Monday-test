import os
import json
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
    """
    # Priority: explicit OTLP endpoint > LANGFUSE_BASE_URL > LANGFUSE_HOST > default
    base = os.getenv("LANGFUSE_OTLP_ENDPOINT") or os.getenv("LANGFUSE_BASE_URL") or os.getenv(
        "LANGFUSE_HOST", "http://localhost:3000"
    )
    endpoint = base.rstrip("/") + "/api/public/otel/v1/traces"
    # Langfuse OTLP export requires the secret key, not the public key.
    api_key = os.getenv("LANGFUSE_API_KEY") or os.getenv("LANGFUSE_SECRET_KEY")
    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    resource = Resource.create({"service.name": service_name})
    provider = TracerProvider(resource=resource)
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
