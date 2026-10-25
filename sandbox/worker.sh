#!/bin/sh
set -eu
socat TCP4-LISTEN:3128,bind=127.0.0.1,fork,reuseaddr UNIX-CONNECT:/ipc/proxy.sock &
pids=$!
OLD_IFS=$IFS
IFS=,
for port in ${CODERILL_PREVIEW_PORTS:-}; do
    [ -n "$port" ] || continue
    socat UNIX-LISTEN:"/ipc/preview-$port.sock",fork,mode=666 TCP4:127.0.0.1:"$port" &
    pids="$pids $!"
done
IFS=$OLD_IFS
sleep 0.1
for pid in $pids; do
    kill -0 "$pid" 2>/dev/null || exit 1
done
exec "$@"
