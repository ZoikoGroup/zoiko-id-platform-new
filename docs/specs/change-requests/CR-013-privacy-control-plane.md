# CR-013: Privacy control plane tables and actions

- **Target:** ZID-DATA-001 §7.2, §9.1; ZID-PRIV-001 §1-19, §27; action catalogue · **Raised by:** Phase F W3 (DEC-F3) · **Status:** Open

## Tables (`privacy` schema, owner: privacy)

| Table | Spec | Note |
|---|---|---|
| `purpose` | DATA §7.2; PRIV §3 | Versioned; immutable once ACTIVE; activation by someone other than the author. |
| `processing_activity` | PRIV §2.1 | New table for the ProcessingActivity object; D3 needs a DPIA reference before activation; `launch_flow` marks flows the DEC-F3 release check covers. |
| `processing_role_assignment` | PRIV §1.1; DEC-F3 §2 | The role map record. Approver ≠ creator; a PROCESSOR role needs its DPA reference. A trigger refuses an ACTIVE activity without an approved, current assignment. |
| `consent_receipt` | DATA §9.1; PRIV §5 | `purpose_code` + `purpose_version` instead of `purpose_id`; adds `jurisdiction_pack_id`, `capture_method`, `affirmative_action`, `receipt_hash`. A terminal consent can never be reactivated (trigger). |
| `preference` | PRIV §18, §21 | Opt-out / limit preferences, including Global Privacy Control signals. |
| `processing_restriction` | PRIV §18 | Restriction and objection, optionally per purpose. |
| `rights_request` | PRIV §13 | The DataRightsRequest. `deadline_source` records where the deadline came from; `COUNSEL_PENDING` when the pack has none. |
| `retention_policy` | PRIV §7 | Versioned; independent approver; `backup_erasure_window_days` nullable until the DPO sets it (DEC-F1 §4). |
| `data_map_entry` | PRIV §10, §17 | The subject-location index: store, object, purpose, data category, retention policy, processor, region, fixed `due_at`. |

## Actions (`note: "CR-013"`)

`privacy.read` (R1), `privacy.rights.manage` (R2), `privacy.rights.fulfill` (R3), `privacy.role_assignment.manage` (R3, second administrator), `privacy.retention.manage` (R3, second administrator), `privacy.processing_activity.manage` (R3).

## Engineering decisions to ratify

1. Jurisdiction-pack fields marked `COUNSEL` are never filled by engineering: a rights request under such a pack is accepted and tracked with `deadline_at = NULL`, `deadline_source = COUNSEL_PENDING` and counted by the worker's deadline monitor.
2. Subject self-service (consent, preferences, own rights requests, own export) needs authentication only; a fresh strong session (AAL-Z2+, ≤15 min) verifies a self-request; opt-outs never need verification (PRIV-15).
3. Legal holds use the evidence module's hold model (CR-011).
4. Stores register a disposer/exporter with `privacy.ports.register_store`; a data-map entry without a disposer stays due and makes the erasure item `FAILED` rather than reporting completion. Wiring every production personal-data store into the data map is a go-live gate (PRIV §32.1 "Data-map discovery").
