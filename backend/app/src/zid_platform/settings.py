"""Typed settings. Environment variables use the ZID_ prefix; no secret defaults (DEC-F5)."""

from zid_app import ServiceSettings

from .roles import Role


class Settings(ServiceSettings):
    service_name: str = "zoiko-id"
    role: Role = Role.CELL
    cell_uuid: str | None = None  # ZID_CELL_UUID: this cell's id in agent.writer_fence (DEC-C5)
