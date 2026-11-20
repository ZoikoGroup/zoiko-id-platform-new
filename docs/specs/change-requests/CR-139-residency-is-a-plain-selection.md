# CR-139: choosing a region is a plain selection

Source: owner decision, 20 Nov 2026. Builds on ZID-EDR-002 (residency offers) and ZID-ENG-DEC-010 (the owner's baseline). Status: built 20 Nov 2026.

## The decision

A company's company verification is already complete when it chooses where its data is kept, so choosing the region is a plain selection: no cell readiness gate, no second administrator, no policy pack to pick, nothing to approve.

## What was built

- `PUT /v1/tenant/residency` no longer calls `require_cell_ready`; the company is placed on the cell that already serves it (moving to another cell is still refused as a migration). The readiness rule stays in the domain and in the public offers page, but the save route does not ask it. Before, a cell that was not marked READY in `cell-readiness.yaml` answered `CELL_NOT_READY`, which no real cell could pass without the five owner sign-offs and the supplier declarations.
- A later change is the owner's own too: the second-administrator approval (`APPROVAL_REQUIRED`) is no longer asked for. The owner's permission and the fresh passkey proof still apply. `approval_request_id` is still accepted from older clients.
- `jurisdiction_pack_id` and `approval_ref` are optional. The pack follows the home cell's legal zone (UK: `uk-gdpr`, EU: `eu-gdpr`, US: `us-ca-ccpa`); an empty reference is recorded as `self-service:<profile>:<cell>`. What is typed is kept.
- The residency form is the home cell (the company's own), the profile and a Confirm button. The legal pack, recovery cells, operator jurisdictions and the reference are under "More options" and optional.

## Risk accepted

A cell that has not passed its readiness gate can now hold a Regional placement, so the gate no longer prevents that promise being made. A recovery cell widens where data may go and is no longer approved by a second person. The offers page still shows each cell's readiness state.

Tests: `tests/unit/test_residency_offers.py`, `tests/component/test_residency_baseline_pg.py`.
