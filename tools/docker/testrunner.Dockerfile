# The Linux test runner that tools/scripts/test-in-docker.sh uses (a Linux Python with uv; the dependencies themselves live in the zid-test-venv volume
# and are synced from uv.lock on every run). Build once:
#   docker build -t zid/testrunner:uv -f tools/docker/testrunner.Dockerfile tools/docker
FROM python:3.13-slim-bookworm
RUN pip install --no-cache-dir uv
