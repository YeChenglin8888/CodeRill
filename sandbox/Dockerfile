FROM node:22.23.2-bookworm AS node

FROM python:3.12.15-bookworm AS sandbox
RUN apt-get update && apt-get install -y --no-install-recommends git bash ripgrep socat ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && git config --system --add safe.directory /workspace \
    && groupadd -g 1000 coderill && useradd -u 1000 -g coderill -m coderill
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx
COPY sandbox/worker.sh /usr/local/bin/coderill-worker
RUN chmod +x /usr/local/bin/coderill-worker
USER coderill
ENTRYPOINT ["/usr/local/bin/coderill-worker"]

FROM ubuntu:resolute-20260912 AS gateway
RUN apt-get update && apt-get install -y --no-install-recommends squid socat ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && squid -v | grep -q 'Version 7\.' \
    && mkdir -p /var/spool/squid /var/log/squid \
    && chown -R proxy:proxy /var/spool/squid /var/log/squid
COPY sandbox/gateway.sh /usr/local/bin/coderill-gateway
RUN chmod +x /usr/local/bin/coderill-gateway
USER proxy
ENTRYPOINT ["/usr/local/bin/coderill-gateway"]
