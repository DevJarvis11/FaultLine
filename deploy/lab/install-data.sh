#!/usr/bin/env bash
# Session 5: PostgreSQL with pgvector and OPA (namespace faultline), MLflow (namespace llmops).
# Generates database passwords on first run and stores them only in Kubernetes Secrets;
# existing Secrets are never regenerated. Nothing secret is printed or written to disk.
# Usage: KUBECONFIG=~/.kube/k3s.yaml deploy/lab/install-data.sh   (run from the repo root)
set -euo pipefail
cd "$(dirname "$0")/../.."

ensure_ns() {
  kubectl get namespace "$1" >/dev/null 2>&1 || kubectl create namespace "$1"
}

# Create Secret $2/$1 with username $3 and a fresh random password, unless it exists.
# The manifest goes over stdin, so the password never appears in argv.
ensure_secret() {
  local name=$1 ns=$2 user=$3 pw
  if kubectl -n "$ns" get secret "$name" >/dev/null 2>&1; then
    echo "secret $ns/$name exists, keeping it"
    return
  fi
  pw=$(openssl rand -hex 24)
  kubectl apply -f - >/dev/null <<EOF
apiVersion: v1
kind: Secret
metadata: {name: $name, namespace: $ns}
type: Opaque
stringData: {username: $user, password: $pw}
EOF
  echo "secret $ns/$name created"
}

ensure_ns faultline
ensure_ns llmops

ensure_secret postgres-superuser faultline postgres
ensure_secret faultline-db faultline faultline
ensure_secret mlflow-db faultline mlflow

kubectl apply -f deploy/lab/postgres.yaml
kubectl -n faultline rollout status statefulset/postgres --timeout=300s

kubectl -n faultline create configmap opa-policies \
  --from-file=actions.rego=policies/actions.rego --dry-run=client -o yaml | kubectl apply -f -
kubectl apply -f deploy/lab/opa.yaml
kubectl -n faultline rollout status deployment/opa --timeout=180s

# MLflow needs only the mlflow password, so copy just that Secret into llmops (via stdin).
if ! kubectl -n llmops get secret mlflow-db >/dev/null 2>&1; then
  kubectl -n faultline get secret mlflow-db -o json \
    | python3 -c 'import json,sys; s=json.load(sys.stdin); print(json.dumps({"apiVersion":"v1","kind":"Secret","type":"Opaque","metadata":{"name":"mlflow-db","namespace":"llmops"},"data":s["data"]}))' \
    | kubectl apply -f - >/dev/null
  echo "secret llmops/mlflow-db copied"
fi
kubectl apply -f deploy/lab/mlflow.yaml
kubectl -n llmops rollout status deployment/mlflow --timeout=600s
