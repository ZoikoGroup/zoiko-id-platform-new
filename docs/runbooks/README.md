# Runbooks

Operational runbooks in the ZID-SRE-001 §22 format. Each file's front matter is checked by
`backend/app/tests/unit/test_operations_catalogue.py` against `backend/config/operations/service-catalogue.yaml`.

| ID | Runbook | Tier | Owner |
|---|---|---|---|
| [RB-01](RB-01.md) | Authorization decision degraded (PDP latency or error burn) | T0 | @zoiko/authorization |
| [RB-02](RB-02.md) | Authentication or token endpoint outage | T0 | @zoiko/identity |
| [RB-03](RB-03.md) | Revocation or security-signal propagation breach | T0 | @zoiko/security |
| [RB-04](RB-04.md) | PostgreSQL writer failover (in-region) | T0 | @zoiko/sre |
| [RB-05](RB-05.md) | Replica lag | T1 | @zoiko/sre |
| [RB-06](RB-06.md) | Database connection exhaustion | T1 | @zoiko/sre |
| [RB-07](RB-07.md) | Outbox or queue lag | T1 | @zoiko/sre |
| [RB-08](RB-08.md) | Poison message | T2 | @zoiko/sre |
| [RB-09](RB-09.md) | Cache or projection outage | T0 | @zoiko/authorization |
| [RB-10](RB-10.md) | Object-store degradation (evidence and backups) | T1 | @zoiko/evidence |
| [RB-11](RB-11.md) | KMS or HSM throttling or outage | T0 | @zoiko/security |
| [RB-12](RB-12.md) | Cell disaster failover | T0 | @zoiko/sre |
| [RB-13](RB-13.md) | Governed tenant migration or failback | T1 | @zoiko/sre |
| [RB-14](RB-14.md) | Backup restore and point-in-time recovery | T1 | @zoiko/sre |
| [RB-15](RB-15.md) | Evidence integrity failure | T0 | @zoiko/evidence |
| [RB-16](RB-16.md) | Signing key compromise or emergency rotation | T0 | @zoiko/security |
| [RB-17](RB-17.md) | Cross-tenant access or authority bypass (SEV0 security incident) | T0 | @zoiko/security |
| [RB-18](RB-18.md) | Bad release rollback and change freeze | T1 | @zoiko/sre |
