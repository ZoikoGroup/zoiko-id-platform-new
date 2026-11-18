# CR-116: screening data in the privacy data map (rights, retention, legal hold)

Source: Wireframe 06 (ZID-DTV-WF-06) section 17, ENG-17, QA-042. Status: engineering built 18 Nov 2026.

## What was built

- `screening/infrastructure/privacy_adapter.py`: the screening module's locator, exporter and disposer for the privacy module's data map. The privacy module keeps owning legal holds, retention schedules and the request workflow; screening only says where a person's data is and how it is disposed. It is registered from the composition root (`register_privacy_stores` in `main.py`), which passes in the privacy module's `register_store`, so screening never imports privacy (the dependency-cycle guard).
- **Found by** the account reference on the case, inside the caller's tenant.
- **Exported** as what the person gave and the fact a check happened (market, purpose, classes, time). Candidates, reasons, dispositions and reviewer notes are never exported: the export says the result is withheld for compliance review. Disclosing a sanctions or PEP result can be unlawful, so that is Legal's decision per case.
- **Disposed** by scrubbing the subject (name, birth, countries, identifiers) and each candidate (matched name, reasons, programs) in place, idempotently. The case row, its source list, the supplier-call log and the append-only decision chain stay.
- Test: `tests/component/test_screening_privacy_pg.py` (found, exported without the result, scrubbed twice, chain and case row untouched, another person's case untouched, registered in the map).

## Not built or not decided

- No retention schedule is set: the privacy default applies (delete on request unless held). The DPO sets a policy code and days.
- The decision chain keeps reviewer free-text rationale, which could mention a name. Removing it needs a Legal decision and a controlled path past the append-only guard; not built.
- Vendor deletion attestation (a licensed provider confirming it deleted what it holds) has no provider to ask.
