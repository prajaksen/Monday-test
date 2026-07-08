# Monday → Kubernetes → Langfuse PoC

## Objective

This repository contains a lightweight proof of concept that demonstrates a future production flow:

Monday.com automation
        ↓
Webhook
        ↓
Shell script
        ↓
Kubernetes
        ↓
PostgreSQL
        ↓
Langfuse

The PoC mirrors the intended production path of:

Monday.com automation
        ↓
CI/CD pipeline
        ↓
AWS EKS
        ↓
PostgreSQL
        ↓
Langfuse

## What is included

- A Flask webhook that accepts HTTP POST requests from an external automation system.
- A shell script that turns Monday payload details into tenant/user mapping SQL and executes it against the PostgreSQL pod with kubectl exec.
- Documentation for local setup, architecture, and a demo walkthrough.
- A sample Helm values file that avoids committing secrets.

## Quick start

1. Create and activate a Python virtual environment.
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Start the webhook:
   ```bash
   python webhook.py
   ```
4. Follow the setup guide in docs/setup.txt for a local Minikube + Langfuse deployment.
5. Send a POST request to the webhook endpoint to trigger the SQL execution path.

Example payload:

```bash
curl -X POST http://127.0.0.1:5000/monday-webhook \
  -H 'Content-Type: application/json' \
  -d '{
    "boardName": "Acme Corp",
    "pulseId": 101,
    "boardId": 202,
    "userEmail": "jane@example.com",
    "userName": "Jane Doe",
    "event": {"type": "status_changed"}
  }'
```

### Webhook endpoints

- GET /healthz returns a simple health response: {"status":"ok"}
- GET / returns a simple home page message: Langfuse Webhook Running
- POST /monday-webhook accepts the Monday-style payload and triggers the SQL update flow

### Troubleshooting connection refused

If you open http://localhost:5000/ and see "Failed to load page" or ERR_CONNECTION_REFUSED, the webhook process is not listening on port 5000 yet.

Check it with:

```bash
curl http://127.0.0.1:5000/healthz
```

If that fails, start the webhook locally:

```bash
python webhook.py
```

If you are accessing the service through Kubernetes instead of locally, run:

```bash
kubectl port-forward svc/langfuse-webhook 5000:80 -n langfuse
```

Then verify with:

```bash
curl http://127.0.0.1:5000/healthz
```

## Security note

Do not commit local secrets such as:

- values-local.yaml
- .env
- Langfuse API keys
- PostgreSQL passwords
- ngrok authtokens
