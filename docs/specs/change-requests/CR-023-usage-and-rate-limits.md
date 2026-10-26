# CR-023: API usage metering and the per-tenant rate limit

- **Target:** ZID-UX-001 §18 (Logs, Rate limits); ZID-DATA-001 §11.1 (metering); ZID-API-001 (rate limiting, 429); action catalogue · **Raised by:** Phase H H-7 · **Status:** Open

## Tables

| Table | Owner | Note |
|---|---|---|
| `metering.usage_event` | metering | Hourly call counts per tenant, API surface, OAuth client and status class (2xx to 5xx). Counts only: no paths with ids, query strings, bodies or personal data. Folded in by upsert. RLS on `tenant_id`. Already declared in `table-ownership.yaml`. `metering.correction` (billing) is not created. |

## API

`GET /v1/usage?days=` (daily calls by surface and status class, and by OAuth client) and `GET /v1/usage/limits` (the tenant's request limit and where it stands this minute). Action `metering.read` (R1/E1).

## Rate limit

A sliding window per tenant, default 600 authenticated requests a minute (`ZID_API_RATE_LIMIT_PER_MINUTE`, 0 turns it off). Over the limit the API answers `429 RATE_LIMITED` with `Retry-After`. It runs after authentication, so unauthenticated traffic cannot spend a tenant's allowance. Tenants cannot raise it. The window is in-process, which is right for one API process per cell; running several API processes per cell needs a shared counter (Valkey), and the edge limiter (DEC-G1) remains the production control against abuse.

## Usage recording

Counts gather in memory and are written in batches every ten seconds (and when read through `/v1/usage`). Usage is telemetry, not billing evidence: a crash can lose the last seconds of counts.
