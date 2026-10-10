# The single Zoiko iD backend image (modular monolith, ADR-0031). Build context: backend/.
#   docker build -t zid/platform .            # runtime image
#   docker build --target test .              # runs the backend test suite (fails the build on failure)
# The same image runs every role and process type:
#   ZID_ROLE=cell|global                       # which modules are served (zid_platform/roles.py)
#   CMD default: HTTP API.  Other processes:  python -m zid_platform.entrypoints.<consumer|worker|outbox_relay>
# Versions are pinned; dependencies come only from uv.lock (--frozen) for reproducible builds (ADR-0027).

ARG PYTHON_IMAGE=python:3.13-slim-bookworm
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.13.0

FROM ${UV_IMAGE} AS uv

# ---------- builder ----------
FROM ${PYTHON_IMAGE} AS builder
COPY --from=uv /uv /usr/local/bin/uv
# Build the venv at its final runtime path so script shebangs stay valid after COPY.
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /src
COPY . .
RUN uv sync --frozen --no-dev --no-editable --package zoiko-id-platform

# ---------- test ----------
FROM ${PYTHON_IMAGE} AS test
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /src
COPY . .
RUN uv sync --frozen --all-packages
RUN uv run --frozen pytest -q libs app

# ---------- runtime: no build tools, non-root ----------
FROM ${PYTHON_IMAGE} AS runtime
RUN groupadd --system --gid 10001 zid && useradd --system --uid 10001 --gid zid --no-create-home zid
COPY --from=builder --chown=zid:zid /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 ZID_ROLE=cell
USER zid
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health/live').status==200 else 1)"
CMD ["python", "-m", "uvicorn", "zid_platform.entrypoints.api:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
