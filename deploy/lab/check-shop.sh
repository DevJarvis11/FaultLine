#!/usr/bin/env bash
# Session 3 done-when checks: Argo CD apps, shop over HTTP, checkout route, TLS cert.
# Usage: deploy/lab/check-shop.sh [traefik-ip]   (default: docker0 gateway 172.19.0.1)
set -euo pipefail
export KUBECONFIG="${KUBECONFIG:-$HOME/.kube/k3s.yaml}"
IP="${1:-172.19.0.1}"
HOST="shop.faultline.lab"

kubectl -n argocd get applications \
  -o custom-columns=NAME:.metadata.name,SYNC:.status.sync.status,HEALTH:.status.health.status

code=$(curl -s --resolve "${HOST}:8000:${IP}" -o /tmp/shop-home.$$ -w '%{http_code}' "http://${HOST}:8000/")
grep -q 'Online Boutique' /tmp/shop-home.$$ && page=ok || page=missing
rm -f /tmp/shop-home.$$
echo "home: HTTP ${code}, shop page ${page}"

# /cart/checkout is POST-only on frontend; GET returning 405 proves frontend answered.
echo "checkout GET: HTTP $(curl -s --resolve "${HOST}:8000:${IP}" -o /dev/null -w '%{http_code}' "http://${HOST}:8000/cart/checkout")"

echo "https: HTTP $(curl -sk --resolve "${HOST}:8443:${IP}" -o /dev/null -w '%{http_code}' "https://${HOST}:8443/")"
echo | openssl s_client -connect "${IP}:8443" -servername "${HOST}" 2>/dev/null \
  | openssl x509 -noout -subject -enddate
