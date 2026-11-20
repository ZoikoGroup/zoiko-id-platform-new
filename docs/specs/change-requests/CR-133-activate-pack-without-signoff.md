# CR-133: put a company-verification pack in force without its sign-offs

Source: owner decision, 20 Nov 2026. Builds on CR-109 (market policy packs, K09). Status: built 20 Nov 2026.

## The decision

A pack is normally published only when it is complete, its checks pass, four roles have signed it (never its author) and, for a high-impact change, two different approvers agree. The owner wants a single **Activate now** button that puts a stored pack in force with none of that, because the pack's only runtime effect today is the retention period of stored company proof documents (`proof.py` reads `retention_days_for`).

## What was built

- `kyb_market_policy.activate(..., without_signoff=True)`: skips the publish checks, still refuses a pack that is already in force, and writes `ACTIVATED_WITHOUT_SIGNOFF: <every missing control>` into the activation note. It returns the skipped controls. Nothing is signed, approved or filled in on anyone's behalf.
- `POST /v1/kyb/market-policies/{hash}:activate-now` with the body `{"acknowledge": "activate-without-signoff"}`. The operator needs the same capability as a normal publish (`kyb.policy.publish`). The audit record carries `without_signoff` and the list of skipped controls.
- The Company verification packs screen has an **Activate now (without sign-off)** button beside Publish and a line saying what it does.

## What it does not change

- The normal publish route and its checks are unchanged and still refuse an unsigned pack (tested).
- A pack activated this way still shows its missing controls; the screen's "incomplete" list is unchanged. Rolling back works as before.
- Verification itself: no claim, grade or company check reads a pack.

## Risk accepted

Documents are then deleted on the pack's retention schedule, which has no recorded legal basis or deletion job. The record says so, but the gap is real until the pack is completed and signed.

Tests: `tests/component/test_kyb_market_policy_pg.py`, `tests/component/test_kyb_policy_api_pg.py`.
