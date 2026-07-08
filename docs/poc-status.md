# Langfuse PoC Status

Status date: 2026-07-08

## Current status

The local proof of concept is mostly working end to end. The Langfuse UI is reachable locally and the webhook-to-PostgreSQL update path has been verified.

## Completed

- [x] Implemented the Flask webhook flow for Monday-style payloads.
- [x] Added the SQL generation/update script for Langfuse tenant/user mapping.
- [x] Added a dedicated Kubernetes service account and RBAC for the webhook pod.
- [x] Applied the Kubernetes manifests and verified the webhook-related permissions path.
- [x] Deployed the local Langfuse stack with Helm.
- [x] Verified the Langfuse web UI is responding at http://127.0.0.1:3000 (HTTP 200).
- [x] Verified the SQL update path against PostgreSQL from the PoC workflow.
- [x] **Completed end-to-end flow:** Monday webhook → Flask app → SQL script → PostgreSQL
- [x] **Verified upsert logic:** Same tenant/user updates are correctly applied via ON CONFLICT.
- [x] **Tested data persistence:** tenant_user_mapping table confirmed in PostgreSQL with correct data.

## Remaining

- [ ] Validate a real Monday payload end to end from the external trigger to visible Langfuse data.
- [ ] Confirm the UI shows the expected tenant/user mapping after a real webhook execution.
- [ ] Add or verify the final CI/CD or external automation trigger path for production-like use.
- [ ] Optionally expose the webhook publicly if a real Monday integration requires an internet-reachable endpoint.

## Langfuse credentials

These values were captured from the local values file used for the PoC deployment.

- Langfuse salt: 4b78035194087c19dc208fd2890e921c97d3bf89595d3e47707cfc3a135bd085
- NextAuth secret: 2c5b85098614f18be1253cd1fea5baaae0e94c47a8f09cfa605a445407a07423
- Encryption key: 97f6986bed18fd62542e025af2164bafe3d2e999505979dbc83e0eb7673cde1a
- PostgreSQL password: 82d63714f0b16ad6b1277c3cda6aef2e
- ClickHouse password: 8fd5d1f3ca022907391cc9acb4ad9f66
- Redis password: 07299eac192ac44492bcd8cb72698d51
- S3 root password: 50809a01a56ba72108ec17d62d7ee9ce

## Access details

- Langfuse UI: http://127.0.0.1:3000
- Webhook service: http://127.0.0.1:5000 (if port-forwarded locally)

> Keep these values local-only and do not commit them to source control.
