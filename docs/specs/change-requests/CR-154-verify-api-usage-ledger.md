# CR-154: Verify API, slice 6: usage report and usage ledger

Status: slice 6 built 24 Nov 2026 (uncommitted). Source: `ZID-EXT-IDV-001` epic E17 (usage ledger integration), WF-15, QA Q38 (meter reconciliation, no double billing).

**Why.** A company needs to see what it used, and the platform needs a record of billable-looking events with unique keys that can be reconciled. The existing request metering (CR-023) is telemetry, explicitly not billing evidence, so this uses the platform's real billable ledger
(`commercial.billable_event`) the way screening and credential checks do.

**The ledger (production only).** Two new meters, `VERIFY_SESSION_START` and `VERIFY_CHECK_COMPLETE` (migration `commercial_0045` widens the closed meter set; keys `vfstart:{session}` and `vfdone:{session}`):
- One fact when a **production** session starts. A repeated create (same idempotency key) is the same session and therefore the same fact.
- One fact when a production session reaches its **first final decision** (verified or not verified). A review or an inconclusive result is not final and writes nothing; a later revision of a final decision is the same session and the same key, so it never counts twice.
- A **sandbox** session writes nothing. The document says sandbox usage is segregated; a test is never usage.
- They are **usage facts only**: no SKU and no price stand behind them, nothing is rated or invoiced until the owners ratify the billable-event policy (D12) and approve a price. The same category as the screening and credential-check meters. The recording never raises, so a ledger problem never blocks a verification.

**The report** (`GET /v1/verify-api/usage?month=2026-11`, `verify_api.application.read`; Developers page, "Usage" card): per application and environment, sessions started in the month and how they stand (verified, not verified, no answer, with a reviewer, canceled, ran out of time), with sandbox and production totals apart, a statement
that nothing is priced yet, and a **reconciliation**: production sessions started and final decisions against the ledger for the month. A difference is shown in words, never hidden.

**Where the recording happens.** `sessions_service` calls `usage_service.meter_session_started` on creating a session and `meter_check_completed` after a decision is written, both guarded by environment. There is no production decision path yet (production sessions are refused until the live engine is connected), so today only the
production-start and completion helpers are exercised by tests, with sessions stood in as production by editing their environment in the test database. The live engine must call the same two helpers.

**Not done / decisions needed.** The meter register (`ZID-ENG-DEC-008`, owner-ratified, with a guard on its exact key set) is untouched: adding register definitions for these two meters is an owner decision. D12 (no-show, abandoned and refund treatment, price and issuer) is open. No export, quota or invoice link yet.

**Tests.** Component (2, real database and HTTP): sandbox is reported apart and writes nothing to the ledger; bad and missing months refused; production sessions metered once each with the right fields, the gap shown before recording and closed after, repeats write nothing, a review resolved later is one completion and revisions add none. Ledger, screening and credential meter suites and the earlier Verify API suites still pass (41). Web: tsc 0 errors,
eslint clean, 21 tests in the developers folder.
