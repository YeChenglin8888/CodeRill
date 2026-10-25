#!/bin/bash
set -eu
squid -N -f /etc/squid/squid.conf &
proxy_pid=$!
ready=0
for attempt in {1..50}; do
    kill -0 "$proxy_pid" 2>/dev/null || exit 1
    if (echo > /dev/tcp/127.0.0.1/3128) >/dev/null 2>&1; then ready=1; break; fi
    sleep 0.1
done
[ "$ready" -eq 1 ] || exit 1
kill -0 "$proxy_pid" 2>/dev/null || exit 1
socat UNIX-LISTEN:/ipc/proxy.sock,fork,mode=666 TCP4:127.0.0.1:3128 &
for port in "$@"; do
    socat TCP4-LISTEN:"$port",bind=0.0.0.0,fork,reuseaddr UNIX-CONNECT:"/ipc/preview-$port.sock" &
done
wait -n
exit 1
