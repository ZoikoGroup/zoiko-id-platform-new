# Public assistant: test traceability

Maps the requirements of ZI-CHAT-ADR-006 (invariants, P0 scenarios, ADRs) and ZI-CHAT-ENG-003 (errors) to the tests that prove them.
Counts are from `pytest --collect-only` on 2026-11-11. A requirement with no row here has no automated proof yet; see
[EVIDENCE_LEDGER.md](EVIDENCE_LEDGER.md) for what still needs people.

## Totals

| Suite | Tests | Where |
|---|---|---|
| Backend unit | 182 | `backend/assistant/tests/unit` |
| Backend security (isolation + P0) | 49 | `tests/security` |
| Backend golden corpus | 101 | `tests/golden` |
| Backend component (Postgres, Valkey) | 9 | `tests/component` (skip when the services are down; CI provides them) |
| **Backend total** | **341** | `python -m pytest assistant/tests` |
| Widget | 27 | `frontend/packages/assistant-widget/test` |
| Platform proxy | 26 | `frontend/apps/web/src/server/assistant-proxy.test.ts` |

Static: `ruff check` and `ruff format --check` clean; `mypy --strict` clean on `assistant/src`; `contract_tool check` current.

## The eight P0 scenarios (ADR-006 section 06)

| P0 | Scenario | Tests (`tests/security/test_p0_scenarios.py`) |
|---|---|---|
| P0-01 | Secret ingestion never reaches inference, embeddings, persistence, handoff or logging | `test_p0_01_secret_ingestion_reaches_no_model_store_log_or_cache` (7 canaries), `test_p0_01_secrets_in_context_slots_and_hints_are_blocked_too`; unit `test_sensitive.py` (26 secret categories, 12 benign, evasion) |
| P0-02 | A public request cannot reach identity routes, even with forged headers | `test_p0_02_the_service_exposes_no_identity_route`, `..._forged_identity_headers_and_cookies_change_nothing`, `..._a_message_cannot_smuggle_identity_fields`; `test_isolation.py` |
| P0-03 | A planned or region-restricted capability is never asserted | `test_p0_03_a_planned_or_region_restricted_capability_is_never_asserted` (4 claims); `test_eligibility.py`; `test_verifier_model.py` |
| P0-04 | Emergency withdrawal blocks in-flight and cached responses | `test_p0_04_emergency_withdrawal_blocks_in_flight_and_cached_responses`, `..._a_withdrawn_link_stops_redirecting_at_click_time` |
| P0-05 | An unchecked purpose creates no record; no false "submitted" | `test_p0_05_...` (3); `test_intake_delivery.py` (consent, idempotency, retry, dead-letter, no receipt on failure) |
| P0-06 | Markup and injection are inert; no arbitrary redirect or unknown CTA | `test_p0_06_...` (3, incl. 8 hostile link ids); `test_verifier_model.py` (11 unsafe shapes) |
| P0-07 | No raw personal input to unapproved models or regions | `test_p0_07_only_sanitized_text_and_approved_evidence_reach_the_model`, `..._no_model_call_when_ai_is_off_or_the_kill_switch_is_on`; `test_claude_request_shape_follows_the_platform_rules` (no tools, no fallback, no extra parameters) |
| P0-08 | The kill switch, a model timeout and abuse leave the website working | `test_p0_08_...` (3); `test_state_stores.py` (flags, breaker, budget) |

## Invariants

| ID | Invariant | Proof |
|---|---|---|
| INV-01 | Public-only authority | `test_isolation.py` (no platform import, no identity library or setting, no identity route) |
| INV-02 | No unverified release claims | `test_eligibility.py`, `test_snapshot.py` (production refuses sample/unsigned), `test_golden_corpus.py` (no launch answer asserts availability or assurance) |
| INV-03 | Pre-model protection | `test_sensitive.py`, P0-01 |
| INV-04 | The model has no authority | `test_verifier_model.py` (minted citations, markup, account assertions, release language), P0-03 |
| INV-05 | Consent is purpose-specific | `test_intake_delivery.py`, P0-05 (no transcript field accepted) |
| INV-06 | Global by design, no inferred market | `test_eligibility.py` (unknown market never matches) |
| INV-07 | Independent shutdown | P0-08, `test_state_stores.py`, Valkey flags test |

## Errors (ENG-003 section 04)

| Code | Test |
|---|---|
| 400 INVALID_REQUEST | P0-02 smuggling, `test_api_transport.py` (message and feedback validation), `test_intake_delivery.py` field validation |
| 401 INVALID_GUEST_SESSION | `test_api_transport.py::test_missing_garbage_and_forgotten_sessions_are_401`; `test_state_stores.py` expiry |
| 403 ORIGIN_DENIED | `test_api_transport.py::test_state_changing_calls_need_an_allowlisted_origin` (4 forged origins x 4 routes), P0-05 |
| 403 REGION_OR_POLICY_BLOCK | P0-05 (forms off by default and for unlisted markets) |
| 409 IDEMPOTENCY_CONFLICT | `test_api_transport.py::test_replay_returns_the_same_packet_and_a_different_text_conflicts`; intake idempotency |
| 413 / 415 | `test_api_transport.py::test_malformed_and_oversized_bodies_are_rejected`, `::test_only_json_is_accepted` |
| 422 SENSITIVE_CONTENT_BLOCKED | P0-01 |
| 429 RESOURCE_LIMIT | P0-08 abuse test; `test_api_transport.py::test_session_creation_is_rate_limited_per_client_address` |
| 503 UNAVAILABLE | P0-08 kill switch; model outage |

## ADR evidence map

| ADR | Evidence |
|---|---|
| 01 Public information plane | isolation tests; refusal of account, identity and credential requests (`test_golden_corpus.py`, 10 safety prompts) |
| 02 Dedicated service | DEC-M1/M2; isolation and role tests; Dockerfile `assistant` target (built from the frozen lockfile) |
| 03 Release registry | snapshot, eligibility, withdrawal tests |
| 04 Constrained RAG | golden corpus, verifier, P0-03/06 |
| 05 Model gateway | gateway, breaker, budget and Claude request-shape tests |
| 06 Versioned API | `test_contract.py` (OpenAPI and API.md match the code), strict-schema tests |
| 07 Ephemeral session | `test_state_stores.py`, Valkey component test |
| 08 Sensitive input | `test_sensitive.py`, P0-01 |
| 09 Detached intake | `test_intake_delivery.py`, P0-05 |
| 10 Global pattern, deferred regions | no regional claim is inferred; forms need an explicit market list |
| 11 Fail closed on withdrawal | P0-04, P0-08; `test_revocations.py` (runtime withdrawal on the next request, restore, click-time link check); Valkey component test (shared across replicas, an outage never un-revokes) |
| 12 Metadata-only telemetry | `test_analytics_tables_have_no_free_text_column`, store-interface guard, P0-01 log check |
| 13 Accessible, validated rendering | widget tests (escaped plain text, link ids only, focus trap, no storage writes) |
| 14 Gated release, independent rollback | runtime kill switch tests; CODEOWNERS on content; evidence ledger |

## Not covered by automation (needs people)

Independent penetration test; manual assistive-technology testing and the device/browser matrix; load and abuse runs at scale; a live
run against the model provider; deployed network deny matrix; kill-switch, withdrawal and provider-outage drills; legal and privacy
review of retention, notices and vendor terms.
