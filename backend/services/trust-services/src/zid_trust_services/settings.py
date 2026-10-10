"""Typed settings for trust-services. Environment variables use the ZID_ prefix; no secret defaults."""

from zid_app import ServiceSettings


class Settings(ServiceSettings):
    service_name: str = "trust-services"
