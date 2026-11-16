# CR-095: Self-run identity verification from open-source parts, with no Veriff and no Sumsub (ZID-EDR-005; ZID-GAP-001 DTV-16; ZID-ED-04)

Status: **engineering path built; NOT a regulated service.** Raised on the engineering owner's instruction of 2026-11-14: complete the identity-verification path with open-source parts instead of a vendor. Veriff and Sumsub are untouched and still work.

**Read this first.** Code cannot make this path acceptable to a regulator, cannot supply a data protection impact assessment, cannot certify presentation-attack detection and cannot evaluate a face engine's accuracy or bias. Those are people's work, they are listed below, and until they are done the biometric half of this path is **off and cannot be switched on**. "Production grade" here means: it fails closed, it keeps nothing it should not, and it refuses to run without the evidence. It does not mean certified.

## What was built

| Part | Where | What it does |
|---|---|---|
| The adapter | `verification/domain/self_run.py`, `verification/application/self_run.py` | Turns a chip passive-authentication verdict, and optionally a face match and a liveness score, into the normalized `ProviderResult` the decision engine already reads. `ProviderKind.SELF_RUN` names it. It is not an adapter row: it is configured by a file, takes no callback, and cannot be registered through the API. |
| The engine port and gate | `verification/domain/biometric_engine.py`, `config/self-run-verification/self-run.yaml`, `verification/infrastructure/self_run_config.py` | A `BiometricEngine` Protocol (match, liveness), a deterministic `FakeEngine` for tests, and a gate that keeps any engine **off** until a complete evaluation record is written. No model ships and no heavy dependency was added. |
| The master-list importer | `evidence/application/csca_master_list.py`, `evidence/infrastructure/csca_import.py` | Verifies an ICAO CSCA master list (CMS, content type 2.23.136.1.1.2) against an anchor the **operator supplies**, takes its CA certificates, and writes them to `config/trust/csca/` for the chip check that already exists. Operator tool, no network, never overwrites, never deletes. `csca/` ships empty and still does. |

## The rules (all fail closed, each has a test)

1. **The chip is the only source of a claim.** `NOT_VERIFIED` gives FAILED, `INDETERMINATE` (no country signing CA configured, unreadable object) gives PENDING. A name or date counts only when it came from a data group whose hash the signed object vouches for, whose check digits hold, on a document that has not expired.
2. **A chip alone is limited.** No binding, no liveness. A profile that needs binding answers `BINDING_FAILED`. The chip check itself says it cannot detect a clone or bind the holder.
3. **A person is bound only when all of these hold:** an accepted engine; a face match and a liveness result from that same engine id and version; the reference photo is the one read from a chip data group whose hash verified (a photo OCR'd from a picture of a document, or a name, never binds); both scores reach the thresholds written with the engine's measured rates.
4. **A face under the threshold is FAILED**, never favourable. A liveness under its threshold keeps the binding, marks liveness failed and raises a presentation-attack signal, which sends the session to review or fails it.
5. **Scores that are not numbers in [0, 1]**, an engine that is not the accepted one, or an engine that raises, give PENDING or an unbound result. Never an approval.
6. **Risk signals pass through** as fraud signals. The path never claims a fraud check was performed, so a profile that requires one fails with `FRAUD_CHECK_UNAVAILABLE`.
7. **The existing decision still applies:** a biometric binding is refused where the profile's jurisdictions do not allow biometrics, and the country privacy overlay and consent check (`privacy_gate`, at session start) are unchanged and still run first.

## The engine gate: what must be written before an engine can run

`enabled: true` is accepted only with **all** of: engine id and version; an independent evaluation id and its date (it expires after 365 days); `pad_standard: ISO/IEC 30107-3`; `licence_checked: true`; a data protection impact assessment reference; the **measured** false accept, false reject, attack accept (APCER) and bona-fide reject (BPCER) rates inside provisional ceilings (0.001, 0.05, 0.05, 0.10, which the owners may only tighten); and thresholds of at least 0.5. An incomplete file is an error that names every missing item, never a silent "off". The shipped file has `enabled: false`.

The gate checks that the references exist and are well formed. It cannot check that the evaluation was honest or that the DPIA is approved: **a person must.**

## Biometric data

A photo or template is a function argument for the length of one call. A result carries the engine id and version, the evaluation id, the thresholds, the two scores (rounded) and a hash commitment. No image, no template, no name and no birth date is kept in the audit record. No table was added.

## Open-source parts and what each needs before use

| Part | Licence note | Needed before it may be switched on |
|---|---|---|
| DeepFace (face match) | MIT; check the licence of each model it downloads | Independent accuracy and bias evaluation on your population; DPIA |
| Silent-Face-Anti-Spoofing (liveness) | Apache-2.0 | ISO/IEC 30107-3 style PAD test by an independent lab; an open model has none |
| InsightFace | code open, **pretrained models non-commercial** | Do not use commercially without a licence |
| JMRTD, ICAO libraries (the phone app reads the chip) | LGPL-class; check | The phone app is not built here; this server only checks what it sends |
| Tesseract, PaddleOCR | Apache-2.0 | Reading text is not proof the document is genuine |

## What is NOT built

- **No HTTP route and no session flow.** The adapter, the engine gate and the importer exist as code and tests. Nothing yet calls them from a verification session, so no user can start a self-run check. Wiring it needs an action in the catalogue, a route, a step-up entry, a session state and a component test: a separate change once the owners accept the path.
- **No phone app that reads the chip**, and no capture of the face or liveness media.
- **No model, no engine adapter.** `FakeEngine` is for tests.
- **No Active or Chip Authentication** and no revocation checking beyond what the chip check already does.
- **No evaluation, no DPIA, no PAD certification, no regulator acceptance.**

## Decisions and work that belong to people

1. **Legal:** which countries accept a self-run check as "verified", and which need a licensed provider.
2. **Privacy:** consent, retention, the DPIA for processing a face yourselves, and the region of processing.
3. **Security:** commission the independent accuracy, bias and PAD evaluations; own false accepts.
4. **Product:** what the product may call a person with no chip document ("verified" versus "review required").
5. **Operations:** obtain each country's master list, confirm anchors out of band, run the importer, review which countries to trust.

## Evidence

`backend/app/tests/unit/test_self_run_verification.py` (engine gate, adapter, application layer, master-list importer, and the chip check trusting an imported CSCA). Acceptance rows `IDV-SR-01` to `IDV-SR-12` in `docs/traceability/acceptance-tests.csv`: none is PASS.
