"""Typed settings for authorization-runtime. Environment variables use the ZID_ prefix; no secret defaults."""

from zid_app import ServiceSettings


class Settings(ServiceSettings):
    service_name: str = "authorization-runtime"
