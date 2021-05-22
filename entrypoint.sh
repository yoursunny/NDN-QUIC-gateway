#!/bin/bash
set -eo pipefail

ACT=$1
shift
if [[ "$ACT" == GATEWAY ]]; then
  exec python gateway.py \
    --cert /etc/ndn-quic-gateway/tls.cert --key /etc/ndn-quic-gateway/tls.key \
    --listen-addr "${ADDR:-::}" --listen-port "${PORT:-6367}" \
    --router-addr "${ROUTER}"
elif [[ "$ACT" == HEALTH ]]; then
  export FLASK_APP=health
  exec flask run \
    --host "${ADDR:-0.0.0.0}" --port "${PORT:-5000}"
else
  exec "$ACT" "$@"
fi
