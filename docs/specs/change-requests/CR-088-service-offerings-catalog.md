# CR-088: Service offerings catalog and the publish-coverage rule

Status: open (catalog, rules and loader built and unit-tested; the API, console screen and public listing are the next steps).

Source: *Zoiko iD Global Service Offerings, Product + Engineering* (revised 2026-11-10), a one-page summary of 12 core offerings with one binding
rule: **publish coverage only when provider rights, country rules, privacy controls and independent tests are verified; design specifications are
not production certification.**

## What changes
- `config/offerings/service-offerings.yaml`: the 12 offerings, each with its customer wording, the country capabilities its entry points are gated
  on, whether it handles personal or biometric data, the ZID-RCG-003 section 12 cases that prove it, and code pointers. It never says where an
  offering is available.
- `verification/domain/offerings.py`: an offering is AVAILABLE in a country only when every capability would be allowed under enforce (pack,
  release, entitlement, no hold), the privacy overlay is usable (and allows biometrics for a biometric offering), and the latest ACCEPTED
  conformance run passes every listed case. Every missing condition is reported. Wording that claims certification is refused at load; such
  claims go through the regulated-claims engine (CR-077).
- `verification/infrastructure/offerings_catalog.py`: the loader.

## Where each offering is built, and what is missing

| Offering | Built in | Gaps found |
|---|---|---|
| Identity & documents | `verification`, `evidence` (v2 documents, extract) | Real document/IDV providers not connected (simulator) |
| Biometrics & fraud | `verification` tiers, `signals`, `screening` signals (human review) | Liveness/face match come only from a provider; none connected. Fairness test T44 is PARTIAL |
| Business verification | `onboarding` org claims, v2 organizations, `screening` | Real company-register sources need contracts (CR-087) |
| Qualifications & standing | `credential` claim checks, cases, practice rules, issuer history | **`credential/api/claim_checks.py` is not behind the country gate**; only `v2_cases` is |
| Verifiable credentials | `credential` issuance, presentations, EUDI class | EUDI registrations config empty |
| Authentication & access | `authentication`, `session`, `federation`, `provisioning` | Not country-gated by design; needs only tests and privacy |
| Delegated authority | `mandate`, `authority`, `machine`, credential v2 authority | T26 PARTIAL |
| Signatures & trust services | `evidence` signatures, ASiC, XAdES, trust-list refresh | Qualified status not yet linked to official-register attestations (RCG-G06); T21 PARTIAL |
| Screening & monitoring | `screening`, monitoring scope approvals | No monitoring approvals configured |
| Privacy & fair review | `privacy`, privacy overlays | No overlay approved; T43, T44 PARTIAL |
| Global APIs & integrations | `webhook`, `integration`, `sdks/`, v2 providers | T35, T40 PARTIAL |
| Evidence & assurance | `evidence`, `assurance`, conformance runs | T45 GAP, T38 PARTIAL |

Today no offering is available anywhere, and that is the intended answer: no conformance run has been accepted, no privacy overlay is approved
and no market is released.

## Next steps
1. `GET /v1/regulatory/offerings` (operator): offering by country with every blocking reason, reusing the enforce-readiness checks (CR-086).
2. Operator console "Service offerings" screen.
3. A public catalog endpoint listing only AVAILABLE offerings with their approved wording, never certification claims.
4. Put `claim_checks` routes behind the country gate.
