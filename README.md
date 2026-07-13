# Langfuse AI Chatbot PoC

## Overview

This project now provides a small Flask-based AI chatbot backed by Ollama and instrumented with Langfuse. It also retains the original Monday webhook workflow so the repository can still support the earlier PoC use case.

## Features

- Flask REST API with a POST /chat endpoint
- Local Ollama integration using llama3.2 by default
- Langfuse tracing with a trace per request and spans for:
  - request validation
  - LLM call
  - response generation
- Health endpoint at GET /health and GET /healthz
- Docker and Docker Compose support
- Kubernetes Deployment and Service manifests

## Local setup

1. Create and activate a Python virtual environment.
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Copy the environment template and adjust the values if needed:
   ```bash
   cp .env.example .env
   ```
4. Run Ollama locally and make sure a model is available. For example:
   ```bash
   ollama pull llama3.2
   ```
5. Start the Flask app:
   ```bash
   python app.py
   ```

## Endpoints

- GET /health returns a simple health response
- POST /chat accepts a JSON payload like:
  ```bash
  curl -X POST http://127.0.0.1:5000/chat \
    -H 'Content-Type: application/json' \
    -d '{"message":"Hello"}'
  ```
- POST /monday-webhook keeps the original webhook workflow

## Docker

Build and run the app with Docker:

```bash
docker build -t langfuse-chatbot .
docker run --rm -p 5000:5000 --env-file .env langfuse-chatbot
```

## Docker Compose

```bash
docker compose up --build
```

## Kubernetes

```bash
kubectl apply -f k8s/deployment.yaml
kubectl port-forward svc/langfuse-chatbot 5000:80 -n langfuse
```

## Security note

Do not commit local secrets such as:

- .env
- Langfuse API keys
- PostgreSQL passwords
