# Multi-stage build for the Candystore Dapr subscriber.
#
# Stage 1: uv resolves and installs dependencies into /app/.venv.
# Stage 2: distroless-ish runtime layer copies the venv and source.

FROM python:3.12-slim AS build

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_LINK_MODE=copy

RUN apt-get update -qq && \
    apt-get install -y -qq --no-install-recommends curl ca-certificates && \
    rm -rf /var/lib/apt/lists/*

# uv via the standalone installer
ADD https://astral.sh/uv/install.sh /uv-installer.sh
RUN sh /uv-installer.sh && mv /root/.local/bin/uv /usr/local/bin/uv

WORKDIR /app

# Resolve deps first for cache stability
COPY pyproject.toml uv.lock* ./
RUN uv sync --frozen --no-dev 2>/dev/null || uv sync --no-dev

# Now bring in the package and re-sync so the local project is installed
COPY src/ ./src/
RUN uv sync --no-dev

FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

COPY --from=build /app/.venv /app/.venv
COPY --from=build /app/src /app/src

RUN groupadd -r candystore && \
    useradd --no-log-init -r -g candystore -d /app candystore && \
    chown -R candystore:candystore /app

USER candystore

EXPOSE 8683 9090

# Liveness — daprd will probe app via its own configured port; we expose
# a cheap stdlib HEAD check via curl in the compose healthcheck.
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=5 \
    CMD python -c "import urllib.request,sys; \
sys.exit(0) if urllib.request.urlopen('http://127.0.0.1:8683/healthz', timeout=2).status==204 else sys.exit(1)" \
    || exit 1

ENTRYPOINT ["candystore"]
CMD ["serve"]
