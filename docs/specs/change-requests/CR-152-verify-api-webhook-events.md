# CR-152: Verify API, slice 4: the company is told when a verification gets a decision or ends

Status: slice 4 built 24 Nov 2026 (uncommitted). Source: `ZID-EXT-IDV-001`, epic E08 (partner webhooks), WF-12.

**Why.** Until now a company's server had to keep asking whether a verification was finished. A company wants to be told. The platform already has a signed, retried, replayable webhook system (`webhook` module, CR for ZID-EVENTS-001), so this slice adds the events and reuses all of that rather than building a second delivery system.

**Two events**, both subscribable from the company's existing webhook endpoints (Developers, Webhooks):
- `com.zoiko.id.verification.api.session_decided.v1`: a session got a decision. Carries session id, application id, the company's own customer reference, status, outcome (`verified`, `not_verified`, `inconclusive`, `review_required`), decision id, decision version, the decision it supersedes, reason codes, policy version and whether it was a sandbox simulation. A review resolved later sends a second event, version 2, that names the first.
- `com.zoiko.id.verification.api.session_closed.v1`: a session ended without a decision: canceled, or ran out of time.

**What never travels:** a launch link or token, a credential or secret, a photograph, document data, a score, the person's country or the company's free-form metadata. The outbox's own guard also refuses key and secret-shaped fields.

**How.** The session service writes the event into the platform outbox in the same database transaction as the change, so an event exists if and only if the change does. The existing consumer routes it to each verified endpoint that subscribed, and the existing worker signs (RFC 9421), delivers, retries and records it. The hosted page has no signed-in company, so the write runs in the session's own company scope (`in_tenant`, from the session row, never from a request).
One company with several applications receives all their events on its endpoints; each event names its application so the receiver can tell them apart.

**Registered in:** `SUBSCRIBABLE` (so it can be chosen and listed in the console), `contracts/asyncapi/proposed-events.yaml` (events catalogue guard) and the regenerated AsyncAPI contract.

**Tests.** Component test (real database and real HTTP): a person finishing on the hosted page, a cancel, a review resolved later (versions 1 and 2, the second naming the first), and an expired fixture produce exactly five events with the right shape and none of the excluded fields; a verified endpoint subscribed to both receives all five, each once, across delivery passes (an endpoint takes a few per pass by design). Architecture, contract and consumer guards pass; the one failing guard is the older `authority/0005` one.

**Not in this slice.** A per-application webhook endpoint (events are per company for now), a console screen that links an application to its deliveries, production gate, usage billing, real capture and engine, SDKs.
