"""Typed settings for global-locator. Environment variables use the ZID_ prefix; no secret defaults."""

from zid_app import ServiceSettings


class Settings(ServiceSettings):
    service_name: str = "global-locator"
