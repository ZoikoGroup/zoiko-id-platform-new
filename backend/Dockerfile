# One Dockerfile for every backend deployable unit (build context: backend/).
#   docker build --build-arg UNIT=identity-runtime --build-arg PACKAGE=zid_identity_runtime -t zid/identity-runtime .
#   docker build --target test .        # runs the backend test suite
# Versions are pinned; dependencies come only from uv.lock (--frozen), so builds are reproducible (ADR-0027).

ARG PYTHON_IMAGE=python:3.13-slim-bookworm
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.13.0

FROM ${UV_IMAGE} AS uv

# ---------- builder: resolve exactly what uv.lock says for one unit ----------
FROM ${PYTHON_IMAGE} AS builder
COPY --from=uv /uv /usr/local/bin/uv
# Build the venv at its final runtime path so script shebangs stay valid after COPY.
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /src
COPY . .
ARG UNIT
RUN test -n "$UNIT" || (echo "build-arg UNIT is required" && exit 1)
RUN uv sync --frozen --no-dev --no-editable --package "$UNIT"

# ---------- test: whole workspace + dev tools; fails the build if any test fails ----------
FROM ${PYTHON_IMAGE} AS test
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /src
COPY . .
RUN uv sync --frozen --all-packages
RUN uv run --frozen pytest -q libs services

# ---------- runtime: no build tools, non-root ----------
FROM ${PYTHON_IMAGE} AS runtime
RUN groupadd --system --gid 10001 zid && useradd --system --uid 10001 --gid zid --no-create-home zid
COPY --from=builder --chown=zid:zid /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
ARG UNIT
ARG PACKAGE
ENV ZID_SERVICE_NAME=${UNIT} ZID_APP_MODULE=${PACKAGE}.entrypoints.api:app
USER zid
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health/live').status==200 else 1)"
CMD ["sh", "-c", "exec python -m uvicorn \"$ZID_APP_MODULE\" --host 0.0.0.0 --port 8000 --no-access-log"]
