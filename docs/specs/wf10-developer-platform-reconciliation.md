# WF10 (Developer console, APIs, SDKs, sandbox, webhooks and MCP): what already exists, what CR-173 added, what is left

Written 25 Nov 2026 for the Zoiko iD product owner. ZID-CP-WF10 v2.0 says to reuse the existing external Verify API and not build a second one. This is the reconciliation from reading the code and running the suites. It makes no production, certification or release claim, and says nothing is proof of a working SDK until an executed integration test exists.

**Headline.** Most of WF10 already exists in the `verify_api` module: applications, credentials (secret, private-key and mTLS), token exchange, rotation with overlap, revocation that applies to issued tokens, sessions with idempotency, a hosted flow, a sandbox simulator, webhook delivery and replay, a go-live gate with a signed grant, usage, cases, a partner OpenAPI and docs. CR-173 adds what the wireframe asks for beyond that.

## Reconciliation by surface

| Surface | Already in the repo | CR-173 adds | Still open |
|---|---|---|---|
| WF-10.01 Developer home | Test Lab, sandbox simulator, quickstart guide | A five-step checklist where the last step needs the partner's server to **confirm a webhook the platform really delivered**; "ready" only then; time to first verified webhook that exists only for finished installs; `verify.api_webhook_confirmation` | No cohort collection from real installs; the quickstart does not yet say which scopes the first call needs; the "15 minutes" is still a hypothesis |
| WF-10.02 Organization and environments | Tenant isolation, sandbox/production split, go-live gate | None | Console cache clearing on a company switch is untested; the region data-path rules are not part of this console; a sandbox token being refused on production has no test because there is no production session path |
| WF-10.03 Applications and scopes | Closed scope list, exact origins, workflow entitlement, production approval | **Versioned scope sets** (`api_scope_change`): staged removal up to 30 days, removal applies to already-issued tokens on the next call, widening a production application needs a second person (database-checked) | Scope sets are not yet bound to a purpose or to a company-wide policy |
| WF-10.04 Credentials | Secret shown once, rotation overlap (24 h, max 7 days), revocation applies on the next call, key and mTLS credentials | A published **credential policy** built from the real constants (`GET /v1/verify-api/credential-policy`) with the tests that prove each statement | No numeric containment objective has been ratified; no log scan for live credentials; no consumer tracking beyond last-used time |
| WF-10.05 API explorer and catalog | Partner OpenAPI, docs page | A sandbox-only **try-it runner** (five fixed calls, refuses production, acts with the scopes the application holds now), **SDK error parity** (one generated table shipped identically in the Python and TypeScript SDKs, with a drift test), a **version catalog** with immutable snapshots and digests (`config/developer/api-versions.yaml`, `contracts/openapi/partner/`), breaking-change and 180-day support rules, a test that the current snapshot equals the code, and a published **error taxonomy** (`/v1/verify-api/errors`); partner 429s carry `Retry-After` | The runner offers five calls, not the whole API; the other SDKs (Go, Java, .NET, native) have no error table |
| WF-10.06 SDK catalog | Seven packages in `sdks/` | An **SDK release register** that refuses GA without a published, signed package with a bill of materials and a test run under 90 days old; download only for GA | **No SDK is signed or published.** `tools/scripts/sdk_release.py` builds a manifest of every file digest and declared dependency, signs it with an Ed25519 key the company supplies, and verifies it (a changed, added or missing file, an edited manifest or another key's signature is refused by name). It holds no key and publishes nothing: the key and the registry account are the company's. Today the register says "No package is generally available" |
| WF-10.07 Hosted flow | Hosted page, theme set of four, return origins, launch link bound to session | None | A cross-company link-theft test and a consent-versus-theme test were not written; there is no separate `/v1/sdk-launch-tokens` route (the launch link is minted with the session) |
| WF-10.08 Sandbox | Simulator with five outcomes | **Eleven versioned personas** including duplicate, out-of-order, stale-signature, tampered-body, schema-drift and slow-endpoint, **deterministic delivery plans** per seed, and a scored report with a digest | The platform does not itself deliver a plan to a partner's endpoint; real-ID-image rejection is not built |
| WF-10.09 Webhooks | Signed deliveries, retries with backoff, replay, SSRF and DNS-rebinding defence | **Endpoint health** by named class (delivered, rejected, error, timed out, could not connect, blocked), with advice | DNS, TLS and refused connections are not told apart; no dead-letter view |
| WF-10.10 Diagnostics | Usage facts, rate limit view | **Request log** (route template, status, error code, trace id; no body, header or person data), **request explorer**, and a **trace** of one session through requests, route decisions, decisions and deliveries | A request that failed authentication is not logged (no application is known); SLIs and billing-admin cost hiding are not built; the log is purged after 90 days by `verify.api_purge_request_log`, a function **nothing schedules yet** |
| WF-10.11 MCP | WF-07 agent gateway enforcing mandate, tool registry and receipts | **RFC 9728 protected-resource metadata** for that gateway, and a **read-only developer documentation MCP server** with a registry that lists the tools deliberately off and why | No company-acting developer tool is offered, by design. The gateway's refused calls now carry the RFC 9728 `resource_metadata` hint, and a token for another audience or issuer is refused (tested) |
| WF-10.12 Certification and go-live | Go-live gate, two-person approval, signed grant | A **scored suite run** kept with its digest, shown beside go-live as informational | The suite does not block go-live: whether it should is a decision for the owners. No runbook scoring. A **claims feed** (`GET /v1/verify-api/claims`) lists what may be said and what may not, built from evidence; a certification claim needs an active record in Zoiko iD's own name. No Trust Center page consumes it yet |

## Claims this platform must not make

- Not "certified", "production ready" or "SDKs available". The register, the credential policy and the suite statement each say what they are not.
- A passed synthetic run is a test record. It is not a certification and not a production approval.
- "Revoked instantly" is replaced by the stated rule: refused on the next call to this API; a stolen but unrevoked token lives at most 15 minutes.

## Found while doing this

- **The console proxy was missing the public catalog routes** the new Errors, SDKs and Claims tabs read (errors, sdks, fixtures, versions, claims, credential-policy): added by name and pinned.
- **A file-order bug in the signing tool**: the manifest depended on the operating system's sort order; it now sorts by relative path.

- **WF09 routing events were undeclared** in the event catalogue (the architecture test only checks a fixed list of prefixes, so it did not notice). They are declared now.
- **The console proxy** had no entry for the new diagnostics and scope-change routes: added and pinned in `bff-allowlist.test.ts`.
- A token carried its own copy of scopes and nothing could change an application's scopes after creation; scope changes now narrow live tokens.

## Left to do

- Sign and publish an SDK: needs the company's signing key and registry account (the tool exists), then a compatibility matrix with executed evidence.
- Ratify a numeric containment objective, and decide whether the sandbox suite must pass before a go-live request.
- Schedule the request-log purge, confirm the 90-day period, and give the other SDKs the error table.
- Collect time-to-first-webhook from real installs.
- A WF-12 Trust Center page that reads the claims feed, and a rollback of the developer platform.
