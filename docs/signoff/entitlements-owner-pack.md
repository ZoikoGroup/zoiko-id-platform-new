# Owner pack: ZID-ENG-DEC-008 commercial entitlements

A checklist for the owners. It approves nothing, and every reference below is blank on purpose. Slice: [L-8](../phases/phase-l-second-wireframe-decisions.md); what engineering built: [CR-045](../specs/change-requests/CR-045-capability-bands-and-usage-reservations.md).

Run `python -m zid_platform.entrypoints.entitlement_report` against the cell database for the live list of differences between the Commercial Estate and the bands.

| # | Ruling needed | Why engineering cannot decide | Reference |
|---|---|---|---|
| 1 | Founder approval of ZID-ENG-DEC-008, its two band tables and the meter register (the config is generated from them) | The record is proposed, not approved | |
| 2 | Map C1 to C5 to signed catalogue SKUs. Engineering used the order of the five plans (`bands` in `capability-bands.yaml`) | Commercial & Finance own the mapping | |
| 3 | Rule on the 48 differences between the Estate and the bands: keep the Estate, or change the Estate, or change the bands. Then set `band_matrix_enforcement` to ENFORCE when the two agree | The record says the Estate stays authoritative; neither side is wrong until you say so | |
| 4 | Correct the two baseline items the Estate lists as Enterprise contract items (registration and recovery, MFA and passkeys) | The record forbids paywalling them | |
| 5 | Confirm or change the feature to capability mapping (`catalogue` on each feature), for example delegation templates to `consent_receipts` | It is an engineering reading of two vocabularies | |
| 6 | Overage: the record approves no automatic overage charge; the Estate rates overage (ZID-COM-001 section 7) | Commercial and legal | |
| 7 | Approve a unit definition, a price and a quota for each meter that should have one (`priced` and `hard_quota` are false today). Meters without a limit will answer a manual-decision hold if made a hard quota | The record approves no number | |
| 8 | Whether a committed reservation should bridge to the billable ledger, and for which meters. The switch exists: `bill_on_commit` in `meter-register.yaml` (default false). Turn it on only for a meter whose operation does not already record itself | Affects invoices | |
| 9 | Approve the four usage events now catalogued as proposed (`commercial.usage.reserved`, `.committed`, `.released`, `commercial.quota.threshold_reached`), and say whether `contract.activated`, `entitlement.snapshot_published`, `entitlement.revoked`, `override.expired` and `service.suspended` are wanted as separate events | Event contract | |
| 10 | The reserve, commit and release routes exist behind the `usage:write` scope. Say which services and workers hold that scope, and confirm the route names | The record calls route names illustrative and platform-owned | |
| 11 | Region wording ("Not available in your region"): which features are region-restricted, and where that list lives | Residency and legal | |
| 12 | The snapshot lifetime (`snapshot_ttl_seconds`, 60 by default, at most 300) | The record asks for a bounded policy TTL and approves no number | |
| 13 | The wording on the Plan features tab ("Included", "Available as an add-on", "Available with upgrade", "Not available in your region") and the downgrade notice | Customer-facing commercial copy | |
| 14 | Which band feature cross-cell identity sharing belongs to (engineering used `standard.credential-verification-validation`) and whether a plan, not only the residency rule, should be able to stop it | The record names the principle, not this feature | |
| 15 | Approve `commercial.entitlement.snapshot_published` as a proposed event, and confirm the snapshot is a shared row (`commercial.entitlement_snapshot`) rather than per process | Event contract and data placement | |
