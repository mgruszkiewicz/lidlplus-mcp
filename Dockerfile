ARG PYTHON_VERSION=3.12
FROM ghcr.io/astral-sh/uv:python${PYTHON_VERSION}-bookworm-slim AS base

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install dependencies first for better layer caching.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

# Copy the project and install it.
COPY lidlbridge ./lidlbridge
COPY README.md ./README.md
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# Receipts, coupons, token, state — mount this as a volume.
VOLUME ["/app/data"]

# MCP server binds here; reverse proxy / tunnel terminates TLS.
ENV LIDL_MCP_HOST=0.0.0.0 \
    LIDL_MCP_PORT=8765 \
    LIDL_TOKEN_FILE=/app/data/refresh_token \
    LIDL_STATE_FILE=/app/data/state.json \
    LIDL_RECEIPTS_DIR=/app/data/receipts \
    LIDL_SCHEDULE=1 \
    LIDL_SCHEDULE_START_HOUR=9 \
    LIDL_SCHEDULE_END_HOUR=21 \
    LIDL_SCHEDULE_EVERY=4 \
    LIDL_SCHEDULE_TZ=Europe/Warsaw

EXPOSE 8765

CMD ["lidl-mcp"]
