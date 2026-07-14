import os
import json
import logging
import requests
import subprocess as _subprocess
import socket
import time
from contextlib import contextmanager
from typing import Any, Dict, Optional

from opentelemetry import trace, propagate
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter, SpanExportResult
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace.sampling import TraceIdRatioBased, ParentBased
from opentelemetry.propagators.composite import CompositePropagator
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from opentelemetry.baggage.propagation import W3CBaggagePropagator
from opentelemetry.instrumentation.flask import FlaskInstrumentor
from opentelemetry.instrumentation.requests import RequestsInstrumentor

try:
    from opentelemetry.instrumentation.psycopg2 import Psycopg2Instrumentor
except Exception:
    Psycopg2Instrumentor = None

logger = logging.getLogger("telemetry")
logger.addHandler(logging.StreamHandler())
logger.setLevel(logging.INFO)


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


def init_telemetry(service_name: str = "langfuse-poc", app=None):
    """Initialize OpenTelemetry SDK and auto-instrument libraries.

    This configures an OTLP/gRPC exporter that sends traces to an OpenTelemetry
    Collector. It auto-instruments Flask and requests, and attempts to
    instrument psycopg2 if available. It also wraps `subprocess.run` to
    emit spans for spawned subprocesses.

    Environment variables:
      OTEL_COLLECTOR_ENDPOINT - gRPC endpoint for collector (defaults to localhost:4317)
      OTEL_SERVICE_NAME - service name override
      OTEL_EXPORTER_OTLP_HEADERS - optional headers in JSON form
      OTEL_INSTRUMENT_PSYCOG - if set to '0' will skip psycopg instrumentation
    """

    collector = os.getenv("OTEL_COLLECTOR_ENDPOINT") or os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT") or "localhost:4317"
    service_name = os.getenv("OTEL_SERVICE_NAME", service_name)
    service_version = os.getenv("OTEL_SERVICE_VERSION") or os.getenv("SERVICE_VERSION") or os.getenv("APP_VERSION")
    deployment_env = os.getenv("DEPLOYMENT_ENV") or os.getenv("ENVIRONMENT") or os.getenv("OTEL_DEPLOYMENT_ENV") or "production"
    cloud_provider = os.getenv("CLOUD_PROVIDER", "aws")
    cloud_platform = os.getenv("CLOUD_PLATFORM", "eks")

    # Optional headers passed as JSON string (for auth) e.g. '{"authorization":"Bearer ..."}'
    headers = {}
    hdrs = os.getenv("OTEL_EXPORTER_OTLP_HEADERS")
    if hdrs:
        try:
            headers = json.loads(hdrs)
        except Exception:
            logger.warning("Invalid OTLP headers JSON: %s", hdrs)

    logger.info("telemetry: collector=%s service=%s", collector, service_name)

    # Resource attributes
    resource_attrs = {
        "service.name": service_name,
        "deployment.environment": deployment_env,
        "cloud.provider": cloud_provider,
        "cloud.platform": cloud_platform,
    }
    if service_version:
        resource_attrs["service.version"] = service_version

    resource = Resource.create(resource_attrs)

    # Sampling: ParentBased(TraceIdRatioBased(probability))
    try:
        samp_prob = float(os.getenv("OTEL_SAMPLE_PROBABILITY", os.getenv("OTEL_TRACES_SAMPLER_ARG", "1.0")))
    except Exception:
        samp_prob = 1.0
    sampler = ParentBased(TraceIdRatioBased(samp_prob))
    provider = TracerProvider(resource=resource, sampler=sampler)

    # BSP tuning
    try:
        schedule_delay = int(os.getenv("OTEL_BSP_SCHEDULE_DELAY_MS", "5000"))
        max_queue = int(os.getenv("OTEL_BSP_MAX_QUEUE_SIZE", "2048"))
        max_batch = int(os.getenv("OTEL_BSP_MAX_EXPORT_BATCH_SIZE", "512"))
        export_timeout = int(os.getenv("OTEL_BSP_EXPORT_TIMEOUT_MS", "30000"))
    except Exception:
        schedule_delay, max_queue, max_batch, export_timeout = 5000, 2048, 512, 30000

    # Determine exporter: attempt to reach collector, otherwise fallback to console exporter
    def _collector_reachable(endpoint: str, timeout: float = 2.0) -> bool:
        try:
            host_port = endpoint
            if ":" not in host_port:
                return False
            host, port = host_port.rsplit(":", 1)
            port = int(port)
            sock = socket.create_connection((host, port), timeout=timeout)
            sock.close()
            return True
        except Exception:
            return False

    exporter = None
    if _collector_reachable(collector):
        # Wrap OTLP exporter with retrying export behavior
        class RetryableOTLPSpanExporter(OTLPSpanExporter):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self._attempts = int(os.getenv("OTEL_EXPORTER_RETRY_ATTEMPTS", "3"))
                self._backoff_ms = int(os.getenv("OTEL_EXPORTER_RETRY_BACKOFF_MS", "500"))

            def export(self, spans):
                last_exc = None
                for attempt in range(1, self._attempts + 1):
                    try:
                        return super().export(spans)
                    except Exception as exc:
                        last_exc = exc
                        logger.warning("OTLP export attempt %d/%d failed: %s", attempt, self._attempts, exc)
                        time.sleep(self._backoff_ms / 1000.0)
                logger.error("OTLP export failed after %d attempts: %s", self._attempts, last_exc)
                return SpanExportResult.FAILURE

        try:
            exporter = RetryableOTLPSpanExporter(endpoint=collector, headers=headers, insecure=True)
            logger.info("Using OTLP/gRPC exporter to %s", collector)
        except Exception:
            logger.exception("Failed to create OTLP exporter, falling back to ConsoleSpanExporter")

    if exporter is None:
        exporter = ConsoleSpanExporter()
        logger.warning("OpenTelemetry Collector not reachable; using ConsoleSpanExporter as fallback")

    processor = BatchSpanProcessor(
        exporter,
        schedule_delay_millis=schedule_delay,
        max_queue_size=max_queue,
        max_export_batch_size=max_batch,
        export_timeout_millis=export_timeout,
    )
    provider.add_span_processor(processor)
    trace.set_tracer_provider(provider)

    # W3C TraceContext + Baggage propagation
    propagator = CompositePropagator([TraceContextTextMapPropagator(), W3CBaggagePropagator()])
    propagate.set_global_textmap(propagator)

    # Auto-instrument Flask and requests. If an app instance is provided,
    # instrument that app explicitly to ensure correct integration.

    # Auto-instrument Flask and requests. If an app instance is provided,
    # instrument that app explicitly to ensure correct integration.
    try:
        if app is not None:
            FlaskInstrumentor().instrument_app(app)
        else:
            FlaskInstrumentor().instrument()
        RequestsInstrumentor().instrument()
    except Exception as exc:
        logger.exception("failed to instrument Flask/requests: %s", exc)

    # Instrument psycopg2 if present and not opted out
    if Psycopg2Instrumentor is not None and os.getenv("OTEL_INSTRUMENT_PSYCOG", "1") != "0":
        try:
            Psycopg2Instrumentor().instrument()
            logger.info("psycopg2 instrumentation enabled")
        except Exception:
            logger.exception("psycopg2 instrumentation failed")
    else:
        logger.info("psycopg2 instrumentation skipped or not available")

    # Setup structured logging to include trace/span/request/tenant metadata
    try:
        setup_structured_logging()
    except Exception:
        logger.exception("failed to setup structured logging")

    # Provide helpers for subprocess tracing and tenant context propagation.
    # Note: we do not monkeypatch subprocess; use `run_subprocess_traced` explicitly.


@contextmanager
def traced_span(name: str, request_id: str, metadata: Optional[Dict[str, Any]] = None):
    tracer = trace.get_tracer(__name__)
    with tracer.start_as_current_span(name) as span:
        _set_span_attribute(span, "request.id", request_id)
        if metadata:
            for key, value in metadata.items():
                _set_span_attribute(span, key, value)
        yield span


def set_tenant_context(context: Dict[str, Any]) -> None:
    """Attach tenant metadata to the current span and to baggage for propagation."""
    try:
        from opentelemetry.baggage import set_baggage

        tracer = trace.get_tracer(__name__)
        span = trace.get_current_span()
        if span is not None and hasattr(span, "set_attribute"):
            for key, value in context.items():
                if value is None:
                    continue
                _set_span_attribute(span, f"tenant.{key}", value)

        # Put core tenant values into baggage for cross-process propagation
        if context.get("tenant_name"):
            set_baggage("tenant.name", str(context.get("tenant_name")))
        if context.get("user_email"):
            set_baggage("tenant.user_email", str(context.get("user_email")))
    except Exception:
        logger.exception("failed to set tenant context")


def run_subprocess_traced(args, **kwargs):
    """Run a subprocess under an explicit OpenTelemetry span.

    Returns the same CompletedProcess object as subprocess.run.
    """
    tracer = trace.get_tracer(__name__)
    cmd = args if not isinstance(args, (list, tuple)) else " ".join(map(str, args))
    with tracer.start_as_current_span("process.exec") as span:
        try:
            _set_span_attribute(span, "process.command", cmd)
            result = _subprocess.run(args, **kwargs)
            try:
                _set_span_attribute(span, "process.return_code", getattr(result, "returncode", None))
            except Exception:
                pass
            return result
        except Exception as exc:
            try:
                _set_span_attribute(span, "process.exception", str(exc))
            except Exception:
                pass
            raise


class OTelLoggingFilter(logging.Filter):
    def filter(self, record):
        try:
            span = trace.get_current_span()
            sc = span.get_span_context()
            if sc is not None and getattr(sc, "trace_id", 0):
                record.trace_id = "{:032x}".format(sc.trace_id)
                record.span_id = "{:016x}".format(sc.span_id)
            else:
                record.trace_id = None
                record.span_id = None
        except Exception:
            record.trace_id = None
            record.span_id = None

        try:
            from opentelemetry.baggage import get_baggage

            record.tenant = get_baggage("tenant.name") or ""
            record.user_email = get_baggage("tenant.user_email") or ""
            # request id may be stored in baggage or span attribute
            record.request_id = get_baggage("request.id") or ""
        except Exception:
            record.tenant = ""
            record.user_email = ""
            record.request_id = ""

        return True


class JsonLogFormatter(logging.Formatter):
    def format(self, record):
        # base log structure
        payload = {
            "ts": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "trace_id": getattr(record, "trace_id", None),
            "span_id": getattr(record, "span_id", None),
            "tenant": getattr(record, "tenant", None),
            "user_email": getattr(record, "user_email", None),
            "request_id": getattr(record, "request_id", None),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload)


def setup_structured_logging(level: int = logging.INFO):
    root = logging.getLogger()
    root.setLevel(level)
    # Avoid duplicate handlers in case of multiple calls
    if not any(isinstance(h, logging.StreamHandler) for h in root.handlers):
        handler = logging.StreamHandler()
        handler.setLevel(level)
        handler.addFilter(OTelLoggingFilter())
        handler.setFormatter(JsonLogFormatter())
        root.addHandler(handler)
    else:
        for h in root.handlers:
            h.addFilter(OTelLoggingFilter())
            h.setFormatter(JsonLogFormatter())
