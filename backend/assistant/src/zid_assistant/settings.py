"""Typed settings (ZID_ASSISTANT_*). Every feature flag defaults to OFF (ZI-CHAT-ADR-006 ADR-14, INV-07).

This service has no identity, tenant, token, signing-key or IAM settings, and must never gain any (INV-01).
Secrets are read from files, never inline (platform rule DEC-F5).
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_HERE = Path(__file__).resolve().parents[2]


class AssistantSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ZID_ASSISTANT_",
        # later file wins: backend/assistant/.env, then the shared backend/.env (only ZID_ASSISTANT_* names are read)
        env_file=(_HERE / ".env", _HERE.parent / ".env"),
        extra="ignore",
    )

    environment: str = "local"  # local | test | dev | sandbox | production
    cell_id: str = "local-1"
    log_level: str = "INFO"
    port: int = 8210
    bind_host: str = "127.0.0.1"

    # --- feature flags (all OFF by default; the kill switch overrides everything) ---
    enabled: bool = False  # assistant_enabled: chat answers at all (static help works regardless)
    ai_answers: bool = False  # assistant_ai_answers: model-drafted answers (OD-010/016/017 first)
    intake_sales: bool = False  # assistant_intake_sales (OD-009..015 first)
    intake_support: bool = False  # assistant_intake_support
    marketing: bool = False  # assistant_marketing election (independent legal sign-off)
    kill_switch: bool = False  # global emergency stop, also flippable at runtime (flags store)

    # --- origins: only first-party web origins may call the API (ADR-06) ---
    allowed_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])

    # --- knowledge (content-as-code, ADR-03) ---
    sql_dir: Path = _HERE / "sql"  # migrations; the image sets ZID_ASSISTANT_SQL_DIR
    content_dir: Path = _HERE / "content"
    snapshot_signature_file: Path | None = None  # detached Ed25519 signature of the snapshot digest
    snapshot_public_key_file: Path | None = None  # publisher verification key (PEM)
    allow_sample_snapshot: bool = True  # engineering samples; production refuses them regardless

    # --- state ---
    valkey_url: str | None = None  # None: in-process store (single replica, tests, local)
    database_url: str | None = None  # runtime role (zid_assistant); None: in-memory stores
    migration_database_url: str | None = (
        None  # owner role, used only by `python -m zid_assistant.migrate`
    )
    runtime_password: str = (
        "zid_assistant_local_only"  # local only; deployed: broker (never inline)
    )

    # --- sessions (RET-02, provisional until Privacy signs; ADR-07) ---
    session_idle_seconds: int = 1800
    session_absolute_seconds: int = 7200
    cookie_name: str = "zid_pa_session"

    # --- request limits ---
    message_max_chars: int = 1000
    rate_limit_window_seconds: int = 600
    rate_limit_messages: int = 30
    rate_limit_sessions: int = 20
    rate_limit_handoffs: int = 6
    cache_ttl_seconds: int = (
        300  # eligibility result cache; withdrawal still checked at serving time
    )

    # --- model gateway (ADR-05): mock until the vendor decision is signed ---
    model_provider: str = "mock"  # mock | claude | groq | gemini
    model_name: str = "claude-opus-5-5"  # a cheaper tier (e.g. claude-sonnet-5-5) is a one-line change once Finance decides (OD-017)
    model_effort: str = (
        "low"  # low | medium | high: Opus 5.5 defaults to medium, so it is set explicitly
    )
    model_api_key_file: Path | None = (
        None  # file holding the provider key (preferred outside local)
    )
    model_api_key: SecretStr | None = (
        None  # local development only: generic key placed in backend/.env
    )
    # Local development: per-provider keys in backend/.env under their plain names (the alias bypasses the ZID_ASSISTANT_ prefix).
    groq_api_key: SecretStr | None = Field(default=None, validation_alias="GROQ_API_KEY")
    gemini_api_key: SecretStr | None = Field(default=None, validation_alias="GEMINI_API_KEY")
    model_timeout_seconds: float = 12.0
    model_max_output_tokens: int = 600
    model_daily_request_budget: int = 20000  # circuit-break above this many calls per UTC day
    model_breaker_failures: int = 5
    model_breaker_cooldown_seconds: int = 60

    # --- intake (WF-10A/B; OFF until OD-013..015) ---
    intake_encryption_key_file: Path | None = (
        None  # 32-byte base64 key; local default is generated in memory
    )
    intake_destinations: dict[str, str] = Field(
        default_factory=dict
    )  # "sales" | "support" -> https receiver URL (allowlist)
    intake_webhook_secret_file: Path | None = None  # HMAC key for the signed receiver payload
    intake_countries: list[str] = Field(
        default_factory=list
    )  # ISO codes where a form is LIVE; empty = forms off everywhere (OD-013)
    intake_notice_sales_id: str | None = (
        None  # signed privacy notice revisions (OD-009/011); unset = that form is unavailable
    )
    intake_notice_support_id: str | None = None
    outbox_max_attempts: int = 5

    # --- retention (RET-*, proposals): configuration, not legal claims ---
    retain_feedback_days: int = 90
    retain_safety_event_days: int = 90
    retain_diagnostic_log_days: int = 14

    @model_validator(mode="after")
    def _origins_fit_the_environment(self) -> AssistantSettings:
        """Outside local/test every allowed origin must be an exact https origin: no wildcard, path, credentials or localhost (ADR-06)."""
        if not self.is_production_like:
            return self
        for origin in self.allowed_origins:
            parts = urlsplit(origin)
            exact = (
                parts.scheme == "https"
                and bool(parts.hostname)
                and parts.hostname not in ("localhost", "127.0.0.1", "::1")
                and "*" not in origin
                and not (parts.username or parts.password)
                and parts.path in ("", "/")
                and not (parts.query or parts.fragment)
                and not origin.endswith("/")
            )
            if not exact:
                raise ValueError(
                    f"ZID_ASSISTANT_ALLOWED_ORIGINS: {origin!r} is not an exact https origin allowed outside local/test"
                )
        return self

    @property
    def is_production_like(self) -> bool:
        return self.environment not in ("local", "test")


def _inline_key(settings: AssistantSettings) -> SecretStr | None:
    if settings.model_provider == "groq" and settings.groq_api_key is not None:
        return settings.groq_api_key
    if settings.model_provider == "gemini" and settings.gemini_api_key is not None:
        return settings.gemini_api_key
    return settings.model_api_key


def has_model_key(settings: AssistantSettings) -> bool:
    """True when a key file or an inline (local) key is configured for the selected provider."""
    return settings.model_api_key_file is not None or _inline_key(settings) is not None


def resolve_model_key(settings: AssistantSettings) -> str:
    """The provider key: the key file wins; otherwise GROQ_API_KEY / GEMINI_API_KEY / ZID_ASSISTANT_MODEL_API_KEY (local/test only)."""
    if settings.model_api_key_file is not None:
        return settings.model_api_key_file.read_text(encoding="utf-8").strip()
    inline = _inline_key(settings)
    if inline is not None:
        if settings.environment not in ("local", "test"):
            raise ValueError(
                "an inline provider key is allowed only in local/test; use ZID_ASSISTANT_MODEL_API_KEY_FILE"
            )
        return inline.get_secret_value().strip()
    return ""
