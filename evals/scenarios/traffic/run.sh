#!/usr/bin/env bash
# Run N k6 generator containers, each with its own source IP, against the lab shop.
# Usage: run.sh organic|flood [-n containers] [-d duration_s] [-v vus] [-r rate] [-p flood_path]
# The generator network is created once and kept; containers are always removed.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IMAGE="grafana/k6:2.4.0"   # Docker Hub grafana/k6 tag 2.4.0 = GitHub release v2.4.0
NET="fl-traffic"
SUBNET="172.31.250.0/24"
GATEWAY="172.31.250.1"
LAB_HOST="shop.faultline.lab"
PORT=8000
MAX_CONTAINERS=40
MEM_LIMIT="96m"

die() { echo "run.sh: $*" >&2; exit 1; }

SCRIPT="${1:-}"; shift || true
[[ "${SCRIPT}" == organic || "${SCRIPT}" == flood ]] || die "first argument must be organic or flood"
N=20; DURATION=""; VUS=""; RATE=""; FLOOD_PATH=""
while getopts "n:d:v:r:p:" opt; do
  case "${opt}" in
    n) N="${OPTARG}" ;; d) DURATION="${OPTARG}" ;; v) VUS="${OPTARG}" ;;
    r) RATE="${OPTARG}" ;; p) FLOOD_PATH="${OPTARG}" ;; *) die "bad option" ;;
  esac
done
[[ "${N}" =~ ^[0-9]+$ ]] && (( N >= 1 && N <= MAX_CONTAINERS )) || die "-n must be 1..${MAX_CONTAINERS}"

# --- Lab-only guard: the shop name must resolve to a docker bridge IP of this machine.
SHOP_IP="$(getent ahostsv4 "${LAB_HOST}" | awk 'NR==1{print $1}')"
[[ -n "${SHOP_IP}" ]] || die "${LAB_HOST} does not resolve"
BRIDGE_IPS="$(ip -4 -o addr show | awk '$2=="docker0" || $2 ~ /^br-/ {split($4,a,"/"); print a[1]}')"
grep -qxF "${SHOP_IP}" <<<"${BRIDGE_IPS}" \
  || die "refusing: ${LAB_HOST} -> ${SHOP_IP} is not a local docker bridge address"

# --- Generator network (idempotent). Refuse if the subnet overlaps anything else.
ip2int() { local IFS=.; read -r a b c d <<<"$1"; echo $(( (a<<24)|(b<<16)|(c<<8)|d )); }
overlaps() {  # overlaps CIDR_A CIDR_B
  local na="${1%/*}" la="${1#*/}" nb="${2%/*}" lb="${2#*/}" l
  l=$(( la < lb ? la : lb ))
  local mask=$(( l == 0 ? 0 : (0xFFFFFFFF << (32 - l)) & 0xFFFFFFFF ))
  (( ($(ip2int "${na}") & mask) == ($(ip2int "${nb}") & mask) ))
}
ensure_network() {
  if docker network inspect "${NET}" >/dev/null 2>&1; then
    local have
    have="$(docker network inspect "${NET}" --format '{{range .IPAM.Config}}{{.Subnet}}{{end}}')"
    [[ "${have}" == "${SUBNET}" ]] || die "network ${NET} exists with subnet ${have}, expected ${SUBNET}"
    return
  fi
  local cidr
  local taken=(10.42.0.0/16 10.43.0.0/16 192.168.1.0/24)
  while read -r cidr; do [[ -n "${cidr}" ]] && taken+=("${cidr}"); done < <(
    docker network ls -q | xargs docker network inspect \
      --format '{{range .IPAM.Config}}{{.Subnet}}{{"\n"}}{{end}}' | grep -E '^[0-9.]+/[0-9]+$' || true
    ip -4 route | awk '$1 ~ /^[0-9.]+(\/[0-9]+)?$/ {print ($1 ~ /\//) ? $1 : $1"/32"}')
  for cidr in "${taken[@]}"; do
    overlaps "${SUBNET}" "${cidr}" && die "subnet ${SUBNET} overlaps ${cidr}"
  done
  docker network create --driver bridge --subnet "${SUBNET}" --gateway "${GATEWAY}" \
    --label faultline.traffic=1 "${NET}" >/dev/null
  echo "created network ${NET} ${SUBNET}"
}
ensure_network

# --- Containers
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)"
PREFIX="fl-k6-${SCRIPT}-${RUN_ID}"
cleanup() {
  docker ps -aq --filter "label=faultline.run=${RUN_ID}" | xargs -r docker rm -f >/dev/null
}
trap cleanup EXIT INT TERM

envs=(-e "TARGET_HOST=${LAB_HOST}" -e "TARGET_PORT=${PORT}" -e "SHOP_IP=${SHOP_IP}")
[[ -n "${DURATION}" ]] && envs+=(-e "DURATION_S=${DURATION}")
[[ -n "${VUS}" ]] && envs+=(-e "VUS=${VUS}")
[[ -n "${RATE}" ]] && envs+=(-e "RATE=${RATE}")
[[ -n "${FLOOD_PATH}" ]] && envs+=(-e "FLOOD_PATH=${FLOOD_PATH}")

echo "run ${RUN_ID}: ${N} x ${SCRIPT} on ${NET} -> ${LAB_HOST} (${SHOP_IP}:${PORT}); start $(date -u +%FT%TZ)"
for i in $(seq 1 "${N}"); do
  docker run -d --name "${PREFIX}-${i}" --network "${NET}" \
    --label faultline.traffic=1 --label "faultline.run=${RUN_ID}" \
    --memory "${MEM_LIMIT}" --cpus 0.5 \
    --add-host "${LAB_HOST}:${SHOP_IP}" \
    -v "${DIR}:/scripts:ro" "${envs[@]}" -e "GEN_INDEX=${i}" \
    "${IMAGE}" run -q --no-usage-report "/scripts/${SCRIPT}.js" >/dev/null
done

# Sample docker memory (sum of generator containers) and node MemAvailable until done.
peak_mib=0; min_avail_mib=999999
while [[ -n "$(docker ps -q --filter "label=faultline.run=${RUN_ID}")" ]]; do
  used="$(docker stats --no-stream --format '{{.MemUsage}}' \
    $(docker ps -q --filter "label=faultline.run=${RUN_ID}") 2>/dev/null \
    | awk '{v=$1; u=v; gsub(/[0-9.]/,"",u); gsub(/[A-Za-z]/,"",v);
            if (u=="GiB") v*=1024; else if (u=="KiB") v/=1024; else if (u=="B") v/=1048576; s+=v}
           END{printf "%d", s}')"
  avail="$(awk '/MemAvailable/{printf "%d", $2/1024}' /proc/meminfo)"
  (( ${used:-0} > peak_mib )) && peak_mib="${used}"
  (( avail < min_avail_mib )) && min_avail_mib="${avail}"
  sleep 5
done

echo "end $(date -u +%FT%TZ)"
fails=0
for c in $(docker ps -aq --filter "label=faultline.run=${RUN_ID}"); do
  code="$(docker inspect -f '{{.State.ExitCode}}' "${c}")"
  docker logs "${c}" 2>&1 | grep -E '^(organic|flood) gen=' || true
  [[ "${code}" == 0 ]] || { fails=$((fails + 1)); docker logs "${c}" 2>&1 | tail -n 3; }
done
echo "generators=${N} failed=${fails} docker_mem_peak_mib=${peak_mib} node_mem_available_min_mib=${min_avail_mib}"
(( fails == 0 ))
