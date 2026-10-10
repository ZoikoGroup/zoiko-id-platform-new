"""Typed settings. Environment variables use the ZID_ prefix; no secret defaults (ADR-0026)."""

from zid_app import ServiceSettings

from .roles import Role


class Settings(ServiceSettings):
    service_name: str = "zoiko-id"
    role: Role = Role.CELL
