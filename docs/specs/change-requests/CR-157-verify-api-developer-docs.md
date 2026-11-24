# CR-157: Verify API, slice 9: developer documentation and the published OpenAPI

Status: slice 9 built 24 Nov 2026 (uncommitted). Source: `ZID-EXT-IDV-001` epic E13 (published OpenAPI, docs and SDK examples) and QA Q48 (a clean-room implementer can complete the first sandbox flow).

**Why.** The API existed but a developer outside the company had no way to learn it: no page, no spec they could download, no worked example.

**The documentation** (`/docs/verify-api`, public, no sign-in, linked from the Developers page): overview, a five-step quickstart with working **curl, Node.js and Python** examples (token, create a verification with an Idempotency-Key, simulate a result, read the decision), authentication and scopes, a table of every operation with the scope it needs, the verification states, the decision shape and how to
handle a superseded decision, the sandbox outcomes, webhooks (both events, signature headers, a receiver example that checks the body digest, de-duplicates by event id and keeps the highest decision version), every error code with what to do, and going live. Examples use a placeholder address and placeholder credentials; nothing is a real secret.

**The published OpenAPI** (`GET /v1/verify-api/openapi.json`, public, cacheable for 5 minutes; the docs page serves it as a download): exactly the **13 operations a partner's server calls**, and only the schemas they use. Console, go-live, usage and hosted-page routes are not in it. The list is explicit (`partner_spec.PARTNER_OPERATIONS`): a new route is not published to partners until it is added there and documented, never by accident.
The full platform contract (`contracts/openapi/public/verify-api.json`) is unchanged in purpose and now also lists this route.

**Held to the facts.** The docs are data, and a test compares them to the code in both directions: the operations documented equal the operations the cell publishes to partners, each exists in the contract with that method, every scope named is a real scope, the error codes and statuses equal the cell's own status map exactly, tables are well-formed, and the examples contain no real-looking secret or token. If the API changes and the page does not, the test fails.

**Not done.** Downloadable SDK packages (the examples are the language coverage until partner evidence selects languages, as the document says); an interactive API explorer; docs for the live service (it does not exist yet); the signature-verification code itself (the page points to an RFC 9421 library and the published key rather than inventing a key format it cannot yet document precisely).

**Tests.** Backend component (2): the served spec is exactly the partner operations, self-contained (every reference resolves) and public; the builder skips an unknown operation and drops unreferenced schemas. Web (6): the drift tests above. Web: tsc 0 errors, eslint clean, 42 tests across developers, docs and the hosted page. All Verify API component suites (37) pass; one usage test timed out once inside the long batch under memory pressure and passes alone.
