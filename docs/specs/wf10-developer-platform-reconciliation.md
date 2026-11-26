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
| WF-10.12 Certification and go-live | Go-live gate, two-person approval, signed grant | A **scored suite run** kept with its digest, shown beside go-live as informational | The suite does not block go-live: whether it should is a decision for the owners. No runbook scoring. A **claims feed** (`GET /v1/verify-api/claims`) lists what may be said and what may not, built from evidence; a certification claim needs an active record in Zoiko iD's own name. A public page at `/docs/verify-api/trust` (relay `/docs/verify-api/claims`) shows it |

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

## Finished in the last round

- **Request-log purge is scheduled**: the maintenance job runs `verify.api_purge_request_log(90)` and reports the rows removed.
- **Sandbox takes synthetic data only**: an email address, a long number or a passport-style line in a reference or metadata is refused with `SANDBOX_REQUIRES_SYNTHETIC_DATA`; nothing is stored.
- **Provider cost is hidden from companies**: a routing decision shown to a company lists provider, rank and p95 latency only; vendor cost stays in the stored decision.
- **Scope rollback**: `POST /v1/verify-api/applications/{id}/scope-changes:rollback` restores an earlier scope set as a new version (version 0 is the original); sessions and keys are untouched.
- **Console cache is scoped to the signed-in identity**: changing company or person drops the previous one's cached screens (`lib/cache-scope.ts`).
- **Log scan found a real leak**: the request log wrote the full path, including a launch link. Values shaped like `zvl_`, `zat_` and `zps_` tokens are now redacted centrally in `zid_telemetry.redaction`. The scan test keeps it from coming back.
- **Cohort report**: `GET /v1/verify-api/onboarding/cohort` gives installs, finished, unfinished and the median seconds to the first verified webhook. Unfinished installs are counted, never averaged in. It covers one company; a cross-company cohort is a platform report and is not built.
- **Error table** copies shipped to the Go, Java and .NET SDKs with a loader and a test in each, run in Docker (Go 1.22, Maven with Java 17, .NET 8): all pass. The revocation objective is set to 5 seconds in the local environment and recorded in `.env.example`; the go-live suite gate stays at shadow until one release of data exists.
- **Trust Center page** for the claims feed (above).

- **SDK release pipeline**: manual workflow (test, build, manifest, sign with the company key in an approval-gated environment, re-verify, publish to PyPI by trusted publishing, npm with provenance, NuGet). A test pins its safety. Building it found the TypeScript SDK never declared `jsdom`, so its widget test could not start; added with a lockfile.
- **Four more rows closed**: a browser bundle secret scan (`tools/scripts/bundle_secret_scan.py`, run in CI after the console build; it never prints a value and an empty scan fails), an axe accessibility test of the hosted capture page, a retried create after a lost response (same session, metered once), and widget tests that completion never says a verification is final.

## Left to do

- Sign and publish an SDK: the pipeline is built (`.github/workflows/sdk-release.yml`, `tools/scripts/sdk_release.py keygen`, `docs/runbooks/sdk-release.md`). What remains is the company's own setup: create the release key, commit its public half, open the registry accounts, add the secrets to the `sdk-release` environment. Java and Go are not automated (GPG key and namespace; module path).
- Ratify the containment objective (`ZID_VERIFY_REVOCATION_OBJECTIVE_SECONDS`) and move the go-live suite gate from shadow to enforce (`ZID_VERIFY_GO_LIVE_SUITE_GATE`). Both are switches now.
- The native bridge has no error table (it is not a Verify API client).
- Rows 6-01 and 6-03 need a published, generally available SDK and executed compatibility runs; row 2-04 belongs to the residency work.
