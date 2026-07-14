OTel Collector Kubernetes manifests

Apply the manifests into the `llm-monitoring` namespace:

```bash
kubectl create ns llm-monitoring || true
kubectl apply -f k8s/otel-collector/serviceaccount.yaml
kubectl apply -f k8s/otel-collector/configmap.yaml
kubectl apply -f k8s/otel-collector/deployment.yaml
kubectl apply -f k8s/otel-collector/service.yaml
kubectl apply -f k8s/otel-collector/hpa.yaml
kubectl apply -f k8s/otel-collector/pdb.yaml
```

Customize the ConfigMap to contain the full collector config (k8s/otel-collector/collector-config.yaml) before applying in production. Ensure the ServiceAccount annotation `eks.amazonaws.com/role-arn` uses your IRSA role.
