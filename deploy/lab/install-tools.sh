#!/usr/bin/env bash
# Install pinned Helm and cilium CLI into ~/.local/bin, verifying SHA-256 checksums.
# Sources: https://github.com/helm/helm/releases (binaries hosted on get.helm.sh)
#          https://github.com/cilium/cilium-cli/releases
set -euo pipefail

HELM_VERSION="v4.3.2"
CILIUM_CLI_VERSION="v0.20.3"
ARCH="amd64"
BIN_DIR="${HOME}/.local/bin"

work="$(mktemp -d)"
trap 'rm -rf "${work}"' EXIT
cd "${work}"

fetch() { curl -fsSLO --max-time 300 --retry 3 "$1"; }

helm_tgz="helm-${HELM_VERSION}-linux-${ARCH}.tar.gz"
fetch "https://get.helm.sh/${helm_tgz}"
fetch "https://get.helm.sh/${helm_tgz}.sha256sum"
sha256sum -c "${helm_tgz}.sha256sum"

cilium_tgz="cilium-linux-${ARCH}.tar.gz"
cilium_base="https://github.com/cilium/cilium-cli/releases/download/${CILIUM_CLI_VERSION}"
fetch "${cilium_base}/${cilium_tgz}"
fetch "${cilium_base}/${cilium_tgz}.sha256sum"
sha256sum -c "${cilium_tgz}.sha256sum"

mkdir -p helm cilium "${BIN_DIR}"
tar -xzf "${helm_tgz}" -C helm
tar -xzf "${cilium_tgz}" -C cilium
install -m 0755 "helm/linux-${ARCH}/helm" "${BIN_DIR}/helm"
install -m 0755 cilium/cilium "${BIN_DIR}/cilium"

"${BIN_DIR}/helm" version --short
"${BIN_DIR}/cilium" version --client | head -n 1
