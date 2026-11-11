# The single Zoiko iD backend image (modular monolith, DEC-D6). Build context: backend/.
#   docker build -t zid/platform .            # runtime image
#   docker build --target test .              # runs the backend test suite (fails the build on failure)
# One container runs the roles listed in ZID_ROLES, each as its own process on its own port:
#   ZID_ROLES=cell (default) -> one process, cell :8000; ZID_SERVE_GLOBAL_PLANE=true adds locator + discovery on that port
#   One database: ZID_CELL_DATABASE_URL (the global role uses it too unless ZID_GLOBAL_DATABASE_URL is set)   (see zid_platform/entrypoints/supervisor.py)
# Other processes use the same image:  python -m zid_platform.entrypoints.<consumer|worker|outbox_relay>
# Versions are pinned; dependencies come only from uv.lock (--frozen) for reproducible builds (DEC-G4).

ARG PYTHON_IMAGE=python:3.13-slim-bookworm@sha256:e32be0e55814a08977501ca863271d9bd02443710db8f2afaf8372e2c4508530
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.13.0@sha256:cdc6093146eb3ff6a40107b38f008b789e050e77ad87865e381d9917da55a168

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
RUN uv run --frozen pytest -q libs app assistant/tests/unit assistant/tests/security assistant/tests/golden

# ---------- public assistant (ZI-CHAT-ADR-006 ADR-02): a SEPARATE image, never the platform image ----------
#   docker build --target assistant -t zid/public-assistant backend/
# Its own dependency closure (no identity libraries), own user, own port (8210). Content and SQL ship beside the venv.
FROM ${PYTHON_IMAGE} AS assistant-builder
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /src
COPY . .
RUN uv sync --frozen --no-dev --no-editable --package zoiko-id-public-assistant

FROM ${PYTHON_IMAGE} AS assistant
RUN groupadd --system --gid 10002 zidpa && useradd --system --uid 10002 --gid zidpa --no-create-home zidpa
COPY --from=assistant-builder --chown=zidpa:zidpa /app/.venv /app/.venv
COPY --chown=zidpa:zidpa assistant/content /app/content
COPY --chown=zidpa:zidpa assistant/sql /app/sql
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1     ZID_ASSISTANT_CONTENT_DIR=/app/content ZID_ASSISTANT_SQL_DIR=/app/sql ZID_ASSISTANT_BIND_HOST=0.0.0.0
# G0 evidence, enforced at build time: this image must contain NO platform code (ADR-02). The build fails if it ever does.
RUN python -c "import importlib.util, sys; sys.exit(1 if importlib.util.find_spec('zid_platform') else 0)"
USER zidpa
EXPOSE 8210
HEALTHCHECK --interval=10s --timeout=5s --start-period=15s --retries=3   CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8210/health/live', timeout=2).status==200 else 1)"
CMD ["python", "-m", "zid_assistant"]

# ---------- runtime: no build tools, non-root ----------
FROM ${PYTHON_IMAGE} AS runtime
RUN groupadd --system --gid 10001 zid && useradd --system --uid 10001 --gid zid --no-create-home zid
COPY --from=builder --chown=zid:zid /app/.venv /app/.venv
# Per-module Alembic streams (DEC-D1), read-only; run with `python -m zid_platform.migrations upgrade`.
COPY --chown=zid:zid app/migrations /app/migrations
# Platform-floor Cedar policies (DEC-C3), evaluated with every tenant release.
COPY --chown=zid:zid policies /app/policies
# Crypto Policy Registry and other reviewed platform configuration (DEC-F5/F6), read-only.
COPY --chown=zid:zid config /app/config
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 ZID_ROLES=cell \
    ZID_MIGRATIONS_DIR=/app/migrations ZID_POLICY_DIR=/app/policies \
    ZID_CRYPTO_POLICY_REGISTRY=/app/config/crypto-profiles/crypto-policy-registry.yaml \
    ZID_JURISDICTION_PACKS=/app/config/jurisdiction-packs ZID_PLATFORM_CONFIG=/app/config
USER zid
EXPOSE 8000
# Healthy only if EVERY configured role answers /health/live on its port.
HEALTHCHECK --interval=10s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "from zid_platform.entrypoints.supervisor import plan; import os,sys,urllib.request; sys.exit(0 if all(urllib.request.urlopen(f'http://127.0.0.1:{p.port}/health/live', timeout=2).status==200 for p in plan(os.environ)) else 1)"
CMD ["python", "-m", "zid_platform.entrypoints.supervisor"]
