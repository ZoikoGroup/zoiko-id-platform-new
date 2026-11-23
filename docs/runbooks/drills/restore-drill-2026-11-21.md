# Restore drill 2026-11-21: PASSED

Run at 20261121-025631 UTC by `tools/scripts/restore_drill.sh` against the rehearsal database `zid_cell` (PostgreSQL 17, one machine, Docker). Engineering's drill, not an independent one.

Backup: 13178450 bytes in 2s. Restore into a new database: 7s (this is the drill's measured restore time on this machine).

## Row counts: live before the backup, restored, live after

| Table | Before | Restored | After | |
|---|---|---|---|---|
| `platform.tenant` | 4 | 4 | 4 | match |
| `core.principal` | 10 | 10 | 10 | match |
| `authz.authority_grant` | 7 | 7 | 7 | match |
| `agent.agent_profile` | 1 | 1 | 1 | match |
| `agent.mandate` | 0 | 0 | 0 | match |
| `agent.build_artifact` | 1 | 1 | 1 | match |
| `agent.workload_binding` | 1 | 1 | 1 | match |
| `agent.api_client_binding` | 0 | 0 | 0 | match |
| `agent.governance_event` | 0 | 0 | 0 | match |
| `agent.run_ledger` | 0 | 0 | 0 | match |
| `evidence.ledger_entry` | 1533 | 1533 | 1533 | match |
| `evidence.checkpoint` | 228 | 228 | 228 | match |
| `evidence.audit_record` | 642 | 642 | 642 | match |
| `evidence.decision_receipt` | 848 | 848 | 848 | match |
| `evidence.evidence_object` | 148 | 148 | 148 | match |
| `keys.key_version` | 13 | 13 | 13 | match |

## Shape

- Migration versions: identical
- Schema: identical (467 tables, 887 indexes, 341 row-level-security policies, 249 triggers)

## Evidence verification on the restored copy

The platform's own verifier recomputed every partition's hash chain, entry signatures, checkpoint roots, links, signatures and timestamp tokens (against the rehearsal timestamp authority's root): every partition verified: {"summary": {"partitions_checked": 6, "partitions_failed": 0, "partitions_errored": 0, "timestamps_verified": 228, "timestamps_unverified": 0}}.

| Partition | Entries | Checkpoints | Timestamp tokens verified | |
|---|---|---|---|---|
| `platform-security:local-1` | 268 | 80 | 80 | verified |
| `tenant:00000000-0000-4000-8000-0000000000e1:local-1` | 36 | 18 | 18 | verified |
| `tenant:01a1f21e-7d61-7b08-a37c-53260a6cbd87:local-1` | 3 | 3 | 3 | verified |
| `tenant:01a1f26c-327e-7c9f-8c0a-337ce954a3ce:local-1` | 1199 | 119 | 119 | verified |
| `tenant:01a1f8a7-d907-7ce2-a8b8-522aada8f1d7:local-1` | 10 | 3 | 3 | verified |
| `tenant:01a1f8ae-62f7-766e-bf8f-35a023236fc4:local-1` | 17 | 5 | 5 | verified |

## What this does and does not show

- It shows the backup restores completely and the restored evidence verifies. The measured restore time is for this machine and this database size (the live database is 57 MB).
- It does not show recovery from the loss of the host, the master key file or the secrets volume: the backup holds wrapped keys, and is useless without the master key (CR-060). The drill that proves those is a restore on a different machine from the backup and the key custodians' copy.
- It is one backup of one point in time. The recovery point of a real deployment depends on how often backups run and whether write-ahead logs are shipped (`deploy/gcp-vm/README.md`).
- The dump file is kept at `~/.zoiko/backups/` outside the repository.
