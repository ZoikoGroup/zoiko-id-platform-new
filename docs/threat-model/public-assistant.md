# Threat model: public assistant

**Scope:** the anonymous information-only assistant (`backend/assistant`, widget, host proxies). **Status:** engineering draft for OD-019 (Security owns the final model and the independent penetration test). Method: trust boundaries, then STRIDE-style threats with the control that answers each and the test that proves it.

## Assets

| Asset | Why it matters |
|---|---|
| Identity platform (tenants, passkeys, tokens, keys) | The crown jewels. The assistant must never be a path to them (INV-01) |
| The truth of public claims | A false "supported / certified / available" is a commercial and legal harm (INV-02) |
| Visitor secrets and personal data typed by mistake | Must not be stored, logged or sent to a model (INV-03) |
| Optional contact details (intake) | Personal data, only after consent (INV-05) |
| Availability of the website | The assistant must fail without taking the site with it (INV-07) |
| Model spend | Unbounded consumption is an attack (OWASP LLM) |

## Trust boundaries

```
Visitor browser ──► host app (platform web / website)  ──► assistant service ──► model provider
   (untrusted)        server-side proxy: allowlist          isolated process,      (untrusted output;
                      headers + 1 cookie, no secrets       own DB role, no IAM     contracted vendor)
                                                            └──► intake receiver (allowlisted https)
```

Nothing from the platform side of the line is reachable from the assistant: no shared process, database, credential, library or setting.

## Threats and controls

| # | Threat (STRIDE) | Control | Proof |
|---|---|---|---|
| T1 | **Elevation:** the assistant is used to reach identity routes or tokens | Separate service, image, DB, role; no identity setting, library, route or outbound client | `test_isolation.py`; P0-02; role denied in `test_postgres_valkey.py` |
| T2 | **Spoofing:** forged identity headers/cookies treated as a user | They are never read; only the assistant's own opaque cookie is accepted; proxy drops everything else | P0-02 forged headers; proxy tests |
| T3 | **Tampering:** a cross-origin or CSRF request starts sessions or posts forms | Origin allowlist on every state-changing call; JSON-only; host-only `SameSite=Lax` cookie | `test_api_transport.py` |
| T4 | **Tampering / over-posting:** extra fields (tenant id, token, image) | Strict schemas reject unknown keys | P0-02 smuggling; intake tests |
| T5 | **Information disclosure:** secrets or ID data pasted into chat reach a log, store or model | Interceptor in request memory before anything else; 422 with category only; no free-text column; log scan | P0-01; `test_sensitive.py`; isolation structural tests |
| T6 | **Information disclosure:** account existence leaks through intake errors | Generic errors, no lookup by email, no echo of input | `test_api_transport.py`; intake tests |
| T7 | **Tampering (prompt injection):** page or message text commands the model | Model has no tools or memory; evidence and message are delimited untrusted data; output is verified, not believed | `test_verifier_model.py`; P0-06 |
| T8 | **Tampering (poisoned content):** a source contains instructions or a false claim | Content-as-code, CODEOWNERS (Product, Legal, Security), signed snapshot, language gate on every answer | `test_snapshot.py`; golden corpus |
| T9 | **Repudiation / integrity:** stale or withdrawn claims keep being served | Serving-time withdrawal checks, click-time link validation | P0-04 |
| T10 | **Tampering (open redirect / XSS):** a link or markup is injected through the answer | Links only by server-issued id; answers rendered as escaped plain text; fixed redirect registry | P0-06; widget tests |
| T11 | **Denial of service:** floods of sessions or messages | Per-client and per-session limits, body cap, edge limiter (T1 in the architecture) | P0-08 abuse test; transport tests |
| T12 | **Denial of service / cost:** runaway model calls | Daily budget, circuit breaker, token cap, timeout; kill switch | `test_verifier_model.py` gateway tests; P0-08 |
| T13 | **Denial of service:** the model or store is down | Verified-excerpt fallback; flag-store outage keeps safe defaults; website independent | P0-08; Valkey flags test |
| T14 | **SSRF:** intake receiver URL points at an internal service | https only, DNS host, no credentials, standard ports, public-address check at connect time, no redirects | `test_intake_delivery.py` |
| T15 | **Information disclosure (data at rest):** a database leak exposes contact data | Field-level encryption of the only personal-data table; key by file; erasure by reference | `test_intake_delivery.py`; component tests |
| T16 | **Spoofing (publisher):** unsigned or tampered content served as approved | Ed25519-signed digest; a production process refuses sample or unsigned content | `test_snapshot.py` |
| T17 | **Elevation (supply chain):** a dependency pulls identity code into the assistant | Dependency allowlist tests; separate lockfile closure for the image | `test_isolation.py`; Docker build |
| T18 | **Misuse:** a visitor treats the bot as support or proofing | Built-in refusals (account, identity, credential), redirect to official routes; no action verbs | golden corpus safety prompts |

## Residual risks and open items

- **No detector is perfect (T5).** Novel secret formats can slip through; mitigated by never persisting text, by the verifier on model output, and by the canary suite growing from incidents. Security must own the corpus and the false-positive review (OD-020).
- **Rate limiting by forwarded address (T11)** trusts the last hop appended by the host's edge. A host without a trusted proxy would let a visitor choose its key; per-session limits still apply.
- **Deployed network deny matrix (T1)** can only be proven where it is deployed (OD-001). The tests prove the code has no path; they do not replace a network policy test.
- **The live model has not been exercised.** Provider behaviour, regional processing and vendor terms are open (OD-010).
- **Sample content.** Everything served locally is labelled engineering sample content; the production gate (T16) exists precisely so it can never be mistaken for an approved publication.
- **Independent penetration test** of the deployed system is still required (G3).
