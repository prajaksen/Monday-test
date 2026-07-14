Quick local Collector (Docker Compose)

1. Start the OpenTelemetry Collector:

```bash
cd docs/otel
docker compose -f docker-compose-collector.yml up
```

2. Run the app (in one terminal):

```bash
# set collector endpoint and service name
export OTEL_COLLECTOR_ENDPOINT=localhost:4317
export OTEL_SERVICE_NAME=langfuse-poc
pip install -r requirements.txt
python3 app.py
```

3. Send a test request and observe logs and collector metrics:

```bash
curl -X POST http://localhost:5000/chat -H 'Content-Type: application/json' -d '{"message":"hello"}'
```

Collector config notes

- The collector config in `collector-config.yaml` exposes an OTLP receiver for gRPC and HTTP.
- The collector forwards traces via the generic OTLP exporter (configure endpoint via env vars when deploying to k8s).

Kubernetes notes

- Deploy the collector as a Deployment or DaemonSet. Use processors `memory_limiter`, `resource`, `batch`, and `k8sattributes` for production.
- Configure exporter to send to Langfuse via the cluster DNS or external endpoint; prefer securing the channel with TLS and auth.

Validation

- Structured logs will include `trace_id`, `span_id`, `tenant`, and `request_id` fields.
- Baggage propagation ensures tenant metadata flows across services.
