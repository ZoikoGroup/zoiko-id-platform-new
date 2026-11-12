# Assistant content (content-as-code)

Engineering SAMPLE content for the public assistant, compiled by the service into one immutable, digest-pinned snapshot.
**Nothing here is approved or published**: answers are `DRAFT_NOT_PUBLISHED`, capabilities `EVIDENCE_PENDING` / `NOT_PUBLIC`, sources
`DRAFT_NOT_PUBLISHED`, no P04 claim is approved and there are no P05 commercial facts. See `manifest.yaml` (`sample: true`).
A production snapshot needs signed P01..P05 records, named owners and gates G1/G2 (ZI-CHAT-CSR-008, ZI-CHAT-ODR-007).

| File | Model (`domain/content.py`) | Contents |
|---|---|---|
| `manifest.yaml` | `Manifest` | snapshot id, version, `sample: true` |
| `intents.yaml` | `Intent` | the 18 intents INT-01..INT-18, routes, priorities, deterministic keywords and phrases |
| `answers.yaml` | `Answer` | the 28 draft answers KB-001..KB-028 (exact Doc 05 text) |
| `p01_capabilities.yaml` | `Capability` | CAP-001..CAP-025 candidates, all `EVIDENCE_PENDING` |
| `p02_sources.yaml` | `Source` | 17 sample sources (PUB-001..PUB-017) with short generic spans |
| `p03_destinations.yaml` | `Destination` | link ids; real website routes `ACTIVE`, authenticated flows `SUSPENDED` |
| `p04_claims.yaml` | `ClaimClass` | CL-01..CL-10 with the gate each `kind` must pass |
| `p05_commercial.yaml` | `CommercialFact` | empty: no commercial fact is approved |
| `copy.yaml` | `CopyItem`, `StarterPrompt` | refusal / abstain / escalate / state copy and six starter prompts |

Rules for editors
- Every YAML key must exist in the model (`extra="forbid"`). Ids must resolve: answer `cta_link_id` to a P03 link, `source_ids` to P02,
  `claim_ids` to P04, `intent_id` to an intent, intent `cta_link_id` / `refusal_key` to P03 / `copy.yaml`.
- Matching text is normalised (lower-case, curly quotes straightened). Write `keywords` / `phrases` / `match` in lower case.
- Entries whose claims include CL-08 (a `SECURE_ROUTE` class) are served as a refusal plus a route, never as an answer. Where an answer's own
  CTA is a `SUSPENDED` secure route, the pipeline uses the intent's active `cta_link_id` instead.
- Never invent a URL. A destination without a verified URL stays `SUSPENDED` with `secure_route` set where it is an authenticated flow;
  its `url` is a `.invalid` placeholder that can never resolve.
- Spans in `p02_sources.yaml` are generic standards explanations. They must not claim that Zoiko iD supports, has released or has certified anything.
