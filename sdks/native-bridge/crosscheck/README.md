# Independent cross-check harness (WF01 and WF03 E03; CR-123)

**Status: contract only. No harness app exists, and nothing here has touched a passport.**

The wireframes allow a self-run chip claim only if our own reader passes independent assurance gates, and they treat JMRTD (Android) and NFCPassportReader (iOS) as research tools, not the product. This folder is the contract between a lab's harness and the gate:

- `run.schema.json`: the one JSON file a run emits (the strict parser is `chip_crosscheck.parse_run`; the lab command is `tools/scripts/chip_crosscheck.py`).
- The plan, device matrix and acceptance rules are in `docs/qa/wf01-chip-crosscheck-plan.md`.

## What a harness app must do

1. Read the **same physical document, on the same phone model, with the same access method**, once with our reader (through the bridge in this folder's parent) and once with the reference library, and write one file per run.
2. Report the **read** (SHA-256 of the raw EF.SOD and of each raw data group) and the **cryptographic checks** (passive authentication, issuer chain, revocation, Active Authentication, chip authentication) **separately**, as the schema's `read` and `crypto` objects. Where the library did not perform a check, write `NOT_RUN` or `NOT_CHECKED`. Never guess a verdict.
3. Write hashes and verdicts only. **No document number, name, date of birth, photo or data-group content in any file.** `document_ref` is a lab id that means nothing outside the lab's own sheet.
4. Use the same trust material for both readers where the library allows it, and say in the lab's notes where it could not be the same.

## Licences

JMRTD is LGPL-3.0 and NFCPassportReader is MIT. Keep each harness a **separate lab tool**: it is never linked into, shipped with or loaded by the product, which keeps the product free of their licence terms and keeps the reference independent of our code. `backend/config/self-run-verification/engine-register.yaml` lists both as `LAB_ONLY` with `commercial_use: UNKNOWN`; that stays true until a person reads the licences and signs the entry.

## What is deliberately not here

No Kotlin or Swift harness source. The libraries' calls must be written against their current documentation by the person building the harness; writing them from memory here would be a guess presented as code.
