"""Alembic env for the `relationship` stream; the shared runner lives in zid_platform.migrations."""

from alembic import context
from zid_platform.migrations import run_env

run_env(context)
