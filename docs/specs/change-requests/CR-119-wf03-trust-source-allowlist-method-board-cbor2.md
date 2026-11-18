# CR-119: trust-source allowlist at validation, the method certification board, and an explicit cbor2 dependency

Source: Wireframe 03 (ZID-DTV-WF-03) T02, T03, T38, screen P10. Status: engineering built 18 Nov 2026.

## What was built

- **Trust-source allowlist wired into the trust store** (`evidence/domain/trust_store.py`, `infrastructure/source_policy.py`, `config/trust/source-allowlist.yaml`): when a staged master list is validated, each country its certificates carry must have the publishing host (the host of the address it was fetched from) recorded as its authority. With `enforce: true` a list that fails is REJECTED at validation and never becomes a candidate; with `enforce: false` (the shipped value) the same findings are attached as `ADVISORY:` reasons. The file ships off and empty and is read strictly. One authority cannot vouch for another country. The same rule exists in `verification/domain/idv_controls.py`; the evidence module cannot import verification, so it is restated in the evidence domain.
- **Method certification board** (`verification/domain/method_certification.py`, `GET /verification-method-certification`, web section on the engine-readiness page): six methods with four honest levels (not built, built but unproven, records missing, approved). Only the face and liveness engine can reach approved, and only when its readiness record says every reference is present. Nothing the platform does alone goes beyond "built, not proven".
- **`cbor2` is now an explicit dependency** (`backend/app/pyproject.toml` and `uv.lock`): three production modules import it directly (mdoc, barcode, device attestation) and it was present only because `webauthn` depends on it.
- Tests: `test_trust_source_policy.py` (unit), `test_trust_source_policy_pg.py` (a real signed master list through validate, enforcement on and off), `test_method_certification.py`, `test_method_certification_pg.py` (over HTTP), `method-logic.test.ts`.

## Not built or not proved

- No authority is recorded and enforcement is off, so no list is refused for its publisher yet. The security owner records the authorities, then turns it on.
- The board reads configuration and the trust store. It is not a place to sign anything: certification evidence (lab reports, DPIA, licence readings) is recorded where it already is (engine and register files).
- `uv.lock` was edited by hand to match `pyproject.toml` because `uv` is not installed here. Run `uv lock --check` before relying on it.
