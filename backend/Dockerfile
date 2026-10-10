# The single Zoiko iD backend image (modular monolith, ADR-0031). Build context: backend/.
#   docker build -t zid/platform .            # runtime image
#   docker build --target test .              # runs the backend test suite (fails the build on failure)
# One container runs the roles listed in ZID_ROLES, each as its own process on its own port:
#   ZID_ROLES=cell,global (default) -> cell :8000 + global :8100   |   ZID_ROLES=cell -> production cell only
#   DBs per role: ZID_CELL_DATABASE_URL / ZID_GLOBAL_DATABASE_URL   (see zid_platform/entrypoints/supervisor.py)
# Other processes use the same image:  python -m zid_platform.entrypoints.<consumer|worker|outbox_relay>
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
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 ZID_ROLES=cell,global
USER zid
EXPOSE 8000 8100
# Healthy only if EVERY configured role answers /health/live on its port.
HEALTHCHECK --interval=10s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "from zid_platform.entrypoints.supervisor import plan; import os,sys,urllib.request; sys.exit(0 if all(urllib.request.urlopen(f'http://127.0.0.1:{p.port}/health/live', timeout=2).status==200 for p in plan(os.environ)) else 1)"
CMD ["python", "-m", "zid_platform.entrypoints.supervisor"]
