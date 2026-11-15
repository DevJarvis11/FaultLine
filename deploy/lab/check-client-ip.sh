#!/usr/bin/env bash
# Session 2 done-when check: requests from two Docker containers on two Docker networks
# must show two distinct ClientHost values in Traefik's JSON access log.
# Usage: deploy/lab/check-client-ip.sh [target-url]
set -euo pipefail

export KUBECONFIG="${KUBECONFIG:-${HOME}/.kube/k3s.yaml}"
TARGET="${1:-http://host.docker.internal:8000/}"
IMAGE="curlimages/curl:8.10.1"
NETS=(fl-ipcheck-a fl-ipcheck-b)

cleanup() { docker network rm "${NETS[@]}" >/dev/null 2>&1 || true; }
trap cleanup EXIT

for net in "${NETS[@]}"; do
  docker network create "${net}" >/dev/null
  docker run --rm --network "${net}" "${IMAGE}" -s -o /dev/null \
    -A "fl-ipcheck/${net}" --max-time 5 -w "${net}: HTTP %{http_code}\n" "${TARGET}" || true
done
sleep 2

hosts="$(kubectl -n ingress logs deploy/traefik --since=1m \
  | grep '"request_User-Agent":"fl-ipcheck/' \
  | grep -o '"ClientHost":"[^"]*"' | sort -u)"
echo "${hosts}"
count="$(printf '%s\n' "${hosts}" | grep -c ClientHost || true)"
if [[ "${count}" -ge 2 ]]; then
  echo "PASS: ${count} distinct client IPs"
else
  echo "FAIL: ${count} distinct client IP(s)"
  exit 1
fi
