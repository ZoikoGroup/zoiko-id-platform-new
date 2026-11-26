# Verify API: the live check (product definition)

Written 25 Nov 2026 for the Zoiko iD product owner and the owners who must accept it. It answers decisions D03 (which methods), D04 (which provider) and D12 (price) for the partner Verify API (ZID-EXT-IDV-001) in the terms the platform can truthfully support today. It is a proposal for acceptance, not an approval: nothing here switches anything on.

## 1. What the check is, in plain words

A partner's customer photographs an identity document and takes a live photograph of their face. Zoiko iD's own engine compares the face on the document with the live photograph. At or above a line the owners set, the result is **verified**. Below it, or when no face can be found, the customer is asked to try again, up to a limit, and then the result is **inconclusive**.

## 2. What the check is NOT (and so what it must never be called)

The platform's own engine states these limits and the product keeps them:

- it runs **no liveness test**: a photograph of a photograph is not detected;
- it does **not check that the document is genuine**: a forged document with a matching face would match;
- it reads no chip.

So the result carries the lowest grade the platform has, `FACE_MATCH_NO_LIVENESS`, and the partner API says exactly that:

| Field | Value |
|---|---|
| `assurance` | `{"scheme": "zoikoid-proofing", "level": "FACE_MATCH_NO_LIVENESS"}` |
| `checks` | one line only: `face_match` (pass or fail). It never lists `document_authenticity` or `face_liveness`, because neither ran. |
| `reason_codes` | a short fixed list (section 6), never an internal engine code, score or accusation. |

The PDF's sample decision (document authenticity pass, face liveness pass) describes a higher grade this engine cannot honestly produce. The sandbox must therefore stop simulating those two checks as if they were part of the live product (see section 8), or a partner will build against a result they will never receive.

Copy rule for every screen, email and document: say "a photograph of the document and a face match". Never say "liveness", "biometric verification", "authentic", "KYC complete" or "approved". A verified result is evidence for the partner's own decision, never the decision.

## 3. Decisions taken in this proposal

| ID | Decision | Why |
|---|---|---|
| D03 | **Document photograph + live selfie, face match.** No chip, no liveness, no document-authenticity claim in version 1. | It is what the engine already does, with its limits already written down. Higher grades are separate, later products. |
| D04 | **Self-run: Zoiko iD's own engine.** No external provider in version 1. | No contract or credentials exist; the engine and its sidecars are in the platform. |
| Route | **Automatic route only for a partner's customers (`WEB_FACE`).** The exception route that sends a case to two human reviewers is **not offered** to partners in version 1. | That route keeps the customer's photographs at Zoiko for review and needs reviewers who are authorized to look at another company's customers. Neither question is answered. After the allowed attempts a case ends `inconclusive` and the partner decides what to do. A reviewed route can be a later product with its own privacy terms. |
| D12 | **Operator-set, effective-dated price per usage fact**, shown as an indicative cost. | Built (CR-161). No price is invented here. |

## 4. What gets kept

- **Automatic route:** neither photograph is kept. Only the decision, its reason codes, the similarity-against-threshold outcome (as pass/fail to the partner) and the policy version are recorded. This is what the engine already does and its tests prove.
- The partner's `customer_reference` is kept with the session; an erasure request removes it and keeps the decision (CR-159).
- No raw media is ever returned by the partner API or its webhooks.

## 5. What the owners must accept before any market can go live

All of these are owner or counsel decisions. The platform enforces them as gates; it does not make them.

1. `ZID_IDV_GLOBAL_POLICY_ACK=accept-global-idv-policy`: the owners' written acceptance of every value in `config/idv/global-policy.yaml` (or a per-country entry in `market-policies.yaml`, which always wins). Values still marked DECISION there: face threshold (the file proposes 0.363, the engine authors' starting point, which is not a legal or accuracy claim), refusal of expired documents, result validity days.
2. `ZID_IDV_FACE_ROUTE_ACK=accept-no-liveness-no-evaluation`: the owners' acknowledgement that the route runs no liveness test and has not been formally evaluated.
3. A lawful basis and notice text for the partner's customer, with Zoiko iD as processor and the partner as controller (decision D06), and a DPIA addendum for face comparison.
4. Per company: the go-live grant must name the country and the check (`face_match`) and an operator must have confirmed it. Production sessions are refused for any country or check the grant does not name.

Until 1 and 2 are set, a production session is refused with `MARKET_NOT_ENABLED` before the customer sees anything. No customer is ever asked for a photograph that cannot be checked.

## 6. What the partner sees, and the words used

Results (partner API and webhooks):

| Outcome | When | Partner-facing reason codes (fixed list) |
|---|---|---|
| `verified` | face match at or above the line | `FACE_MATCHES_DOCUMENT_PHOTO` |
| `inconclusive` | no face found, unclear photograph, below the line after the allowed attempts, or the engine unavailable | `FACE_NOT_FOUND_OR_UNCLEAR`, `FACE_DOES_NOT_MATCH_DOCUMENT_PHOTO`, `ATTEMPTS_USED_UP`, `ENGINE_UNAVAILABLE`, `DOCUMENT_EXPIRED`, `DOCUMENT_UNREADABLE` |
| `not_verified` | not used by the face route: a low similarity is never stated as an accusation | none |

A below-threshold face is **inconclusive, never not_verified**: the engine cannot tell a different person from a bad photograph, and the product will not say "this person is not who they claim".

## 7. The customer's screens (design principles)

1. **Who is asking and why**, first: the partner's name (never replaced by ours), purpose, what will be photographed, that Zoiko iD processes it for the partner, how long anything is kept (nothing, for this route), and a way to stop. Consent is a plain button, not a pre-ticked box; declining ends the session with no capture.
2. **One thing per screen**: document, then selfie, then a result. A progress line ("Step 2 of 3"), a camera frame with a face oval, live guidance (too dark, move closer), and a retake always one tap away.
3. **Truthful states**: "We have your photographs and are checking them" (not "approved"); "We could not see your face clearly: try again in better light" with attempts left shown ("2 tries left"); at the end "Thank you. You can return to {partner}." The customer is never shown a score, a threshold or the word "fraud".
4. **No dead ends**: camera denied gives plain steps and a way to continue on another device; an expired link says how to ask the partner for a new one.
5. **Accessible**: keyboard and screen-reader complete, captions for any instruction, language chosen by the partner or the customer, tested against WCAG 2.2 AA before any market goes live (Q42).
6. **Safe by default**: the page cannot be framed, takes no instruction from the address bar, shows only the partner's registered return address, and never stores a photograph in the browser.

## 8. Changes this definition implies elsewhere

- **Sandbox honesty:** the simulator offers fixtures for outcomes the live product never produces (document authenticity, liveness). Either remove those checks from sandbox decisions, or label them clearly as sandbox-only, so partner tests match production. Recommended: remove.
- **Product catalogue and go-live form:** a production grant can name only `face_match`; `document_authenticity`, `face_liveness` and `nfc_chip` stay sandbox-only and are shown as "not available yet", not silently offered.
- **Workflows:** production serves `wf_document_selfie` only. `wf_document_only` has no route that can produce a verified result and is refused in production.
- **Usage:** one billable "check complete" fact when a session first reaches `verified` or `inconclusive` after the customer submitted photographs (decision D12 treatment of abandoned sessions is still open).

## 9. What would change this

A liveness engine that has been evaluated, a document authenticity engine, a chip read, a contracted provider, or a reviewed route each raises the grade and each needs its own acceptance, its own assurance label and its own price. None is implied by this definition.
