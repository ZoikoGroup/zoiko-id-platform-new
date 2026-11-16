# CR-101: Compliance cases console, identity signals into the review queue, and the Travel Rule message record (ZID-GAP-001 v2.0 DTV-31, DTV-20/21/22, DTV-33)

Status: **built on the engineering owner's instruction; the decisions below are engineering decisions, not owner approvals, and the sign-off table is UNSIGNED.** The Travel Rule routes still refuse every call while the `travel_rule` product line is DESIGN. No provider and no partner is connected.

## Decisions taken (each can be reversed by the owners)

1. **Which plane owns the compliance console (DTV-31): the company plane.** It lists only the company's own work, read through the routes that already exist, each under its own permission. The reviewer queue for signals stays on the operator side. The console never joins operator records and company records in one screen, and it adds no new data path between planes.
2. **Role matrix for the signal feed: none needed.** Identity signals open items in the existing reviewer queue (`/v1/reviews/signals`), which is already a reviewer-only route behind protected actions. Nothing is shown to a tenant role that could not see it before.
3. **The Travel Rule message record keeps findings, not people.** Legal has not set a retention rule for message contents, so the record holds a hash, the two VASP identifiers (companies), the counterparty and threshold answers, finding codes and the registry version. The payload is not stored. When Legal and the partner contract set retention, storing the payload is a new decision.

## What was built

- **Compliance cases console**: `/admin/compliance` (`features/admin/compliance`). Two sections: source of funds cases that need review, and verification workflow runs waiting for a person. A source the role may not read says "Withheld for your role", not "none". A switched-off line says "Not available". Rows link to where a person decides; the page has no button that approves, clears or declines. In the menu and the guide for administrators only.
- **Identity signals into the queue**: `verification/application/identity_signal_feed.py` builds findings from the CR-098 link graph and shared-device rules (`IDENTITY_LINK_WEAK|MODERATE|STRONG`, `DEVICE_SHARED_BY_MANY`); `screening/application/signals.py` `raise_identity_findings` opens one review item per group (a repeat adds nothing) with counts only; `screening_0005` allows the source `IDENTITY_SIGNAL`. The recommendation is review, always. Nothing blocks, approves or clears anybody, and no phone, email or device id is carried, not even hashed.
- **Travel Rule message record**: `verification_0017` (`verify.travel_rule_message`, row-level security, append-only by trigger), `travel_rule_store.py`, `travel_rule_service.py`, and `POST /v2/travel-rule/messages`, `GET /v2/travel-rule/messages`, `GET /v2/travel-rule/messages/{id}`. Actions `verification.travel_rule.record` (R2, E2) and `verification.travel_rule.read` (R1, E1). The same message in the same direction is recorded once. A malformed message is recorded too, with its finding codes. Nothing is sent to a partner and nothing is decided about the transfer.

## The rules (each has a test)

1. The shipped DESIGN line refuses recording (409 `service_unavailable`).
2. A recorded message holds no name and no account number, and never changes or gets deleted.
3. The same identity group opens one item; only a person's ruling closes it; one company never sees another's items.
4. The console only reads: no decision control, and a role without a source's permission sees it withheld.

## What it does not do

- It does not feed observations to the identity-signal rules by itself. A caller supplies hashed observations; no sign-up or session hook calls it yet, because which events count as observations is a product decision.
- The compliance console has no signal queue section: that queue has no company-side screen and stays with the reviewers.
- No message is sent or received over a Travel Rule network; there is no partner.

## Owner decisions still needed

1. Whether the compliance console should ever include reviewer-plane queues, and for which roles.
2. Which sign-up and session events feed identity observations, and for how long observations are kept.
3. How long, and where, the contents of a Travel Rule message may be kept (Legal, with the partner contract).

## Evidence

`backend/app/tests/component/test_identity_signal_feed_pg.py`, `test_travel_rule_messages_pg.py`, `frontend/apps/web/src/features/admin/compliance/compliance.test.tsx`.

## Sign-off (UNSIGNED)

| Role | Name | Date | Decision on the three questions above |
|---|---|---|---|
| Product owner | | | |
| Compliance officer | | | |
| Legal | | | |
