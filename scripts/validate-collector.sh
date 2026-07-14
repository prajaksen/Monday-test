#!/usr/bin/env bash
set -euo pipefail

NS=llm-monitoring

echo "Checking namespace $NS"
kubectl get ns $NS >/dev/null 2>&1 || { echo "Namespace $NS not found"; exit 2; }

echo "Checking collector pods"
kubectl get pods -n $NS -l app=otel-collector -o wide

echo "Waiting for ready pods (60s)"
kubectl wait --for=condition=Ready pod -l app=otel-collector -n $NS --timeout=60s

echo "Checking service"
kubectl get svc otel-collector -n $NS -o wide

# Check metrics endpoint
POD=$(kubectl get pods -n $NS -l app=otel-collector -o jsonpath='{.items[0].metadata.name}')
if [ -z "$POD" ]; then
  echo "No otel-collector pod found"; exit 2
fi

echo "Fetching /metrics from pod $POD"
kubectl exec -n $NS $POD -- curl -sf http://localhost:8888/metrics | head -n 20 || { echo "metrics endpoint not reachable"; exit 3; }

echo "Validation OK"
