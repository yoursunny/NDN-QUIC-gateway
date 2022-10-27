#!/bin/bash
set -euo pipefail

ACT=$1
shift
if [[ "$ACT" == GATEWAY ]]; then
  exec python gateway.py \
    --cert /etc/ndn-quic-gateway/tls.cert --key /etc/ndn-quic-gateway/tls.key \
    --listen-addr "${ADDR:-::}" --listen-port "${PORT:-6367}" \
    --router-addr "${ROUTER:-127.0.0.1:6363}"
elif [[ "$ACT" == HEALTH ]]; then
  exec uwsgi --wsgi-file health.py --callable app --http-socket "${ADDR:-0.0.0.0}:${PORT:-5000}"
else
  exec "$ACT" "$@"
fi
