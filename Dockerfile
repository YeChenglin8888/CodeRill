FROM node:22-bookworm-slim AS web-build
WORKDIR /build/web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/index.html web/tsconfig.json web/vite.config.ts ./
COPY web/src ./src
RUN npm run build

FROM python:3.12-slim-bookworm AS runtime
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HOME=/home/coderill
RUN apt-get update \
    && apt-get install -y --no-install-recommends bash git ripgrep \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml README.md ./
COPY coderill ./coderill
# The current web entry point resolves its assets beside the source package.
RUN pip install --no-cache-dir -e ".[mcp]" \
    && groupadd --gid 1000 coderill \
    && useradd --uid 1000 --gid 1000 --create-home coderill \
    && mkdir -p /workspace /home/coderill/.coderill \
    && chown -R coderill:coderill /workspace /home/coderill
COPY --from=web-build /build/web/dist ./web/dist
USER coderill
EXPOSE 18765
HEALTHCHECK --interval=15s --timeout=3s --start-period=15s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:18765/healthz', timeout=2).close()"]
CMD ["python", "-m", "coderill.app.web", "--host", "0.0.0.0", "--port", "18765", "--cwd", "/workspace"]
