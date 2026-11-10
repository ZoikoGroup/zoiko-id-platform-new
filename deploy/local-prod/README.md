# Local rehearsal of the production deployment

Run the whole product on one machine **from the real production images**, set up the way `deploy/gcp` sets up production, so you can see what a deployment consists of and what is still open. Synthetic data only.

```bash
cd deploy/local-prod
./rehearse.sh up        # builds zid/platform + zid/web images, creates databases and roles, migrates, starts everything
./rehearse.sh status    # what runs, what is healthy
./rehearse.sh logs cell # logs of one service
./rehearse.sh down      # stop, keep data         ./rehearse.sh reset   # stop and delete all data and generated secrets
```

Needs Docker Desktop, Git Bash (or any POSIX shell) and `openssl`. The first run builds two images (about 5 to 15 minutes); later runs reuse them (`up --rebuild` rebuilds).

| Open | What |
|---|---|
| http://localhost:13000 | the web console (Next.js production build, standalone) |
| http://localhost:18000/docs | the cell API (OpenAPI); the global plane (locator and discovery) is served here too |
| http://localhost:18025 | Mailpit: every email the platform sends (sign-up, recovery) lands here |
| `localhost:15432` | PostgreSQL, user `postgres`, password in the generated `.env` |

Ports are offset (`13000`, `18000`, `15432`) so this runs beside the everyday dev stack (3000, 8000).

## What `up` does, and where production differs

| Step | The rehearsal | Real production (`deploy/gcp`) |
|---|---|---|
| Images | `docker build` of `backend/` and `frontend/` on your machine | the release workflow builds, scans, attests and signs them by digest; `deploy.sh` verifies the signatures |
| Database | one Postgres 17 container, one platform database (`zid_cell`) | Cloud SQL, private IP only, point-in-time recovery, pgaudit; the global plane shares it |
| Roles | `zid_owner` (migrations only), `zid_runtime` (API, row-level security applies), `zid_worker` (BYPASSRLS, sweeps every tenant) from `deploy/gcp/db/*.sql` | the same scripts, run on the VM against Cloud SQL |
| Migration | one-shot `migrate` container as the owner role | the same, run by `deploy.sh` before the cell restarts |
| Keys | development keys persisted in a Docker volume (`ZID_LOCAL_KEYSTORE`) plus generated `ZID_DEV_SIGNING_KEYS` | **Cloud KMS HSM**: one key per purpose, never exported; created by OpenTofu |
| Secrets | generated into `.env` | OpenBao, auto-unsealed by Cloud KMS, logged into with the VM's Google identity |
| `ZID_ENVIRONMENT` | `local` for the backend (see below) | `production` |
| Email | SMTP relay is a Mailpit sink, no TLS | a real SMTP provider with SPF, DKIM and DMARC on the sender domain |
| Edge | none: plain HTTP on localhost | Google load balancer with managed certificates, Cloud Armor, or Caddy with automatic TLS |
| DNS and passkeys | relying-party id `localhost` | `id.`, `api.`, `global.` under your domain; the domain is the passkey relying-party id |
| Operator console | not started | `--profile operator`, restricted by IP allow-list or Identity-Aware Proxy |

**Why the backend runs with `ZID_ENVIRONMENT=local`:** the code deliberately refuses to start in `production` with local keys, an environment-variable secret broker or development simulators (DEC-F5; `crypto_runtime.py`, `main.py`). Those checks need Cloud KMS and OpenBao, which cannot exist on a laptop. So the rehearsal proves everything except the key and secret services. The first real proof of those is `tools/scripts/gcp_kms_smoke.py` on the VM, before taking traffic.

## What the first rehearsal found (and what was fixed)

Running it the production way exposed three things the everyday dev stack hides, because the dev stack migrates as a superuser:

1. **A migration failed for the production owner role** (`commercial` 0021, the payment backfill): `invoice` and `payment_attempt` force row-level security, which also binds their owner, and `SET LOCAL row_security = off` only helps a superuser or BYPASSRLS role. Fixed: the force is lifted for that one statement and restored. A real deploy would have stopped at the migrate step.
2. **`deploy/gcp/db/02_grants.sql` never granted anything on the global database** (`global_directory` was not in its schema list), so the global API reported `not_ready` and, once ready, could not have deleted retired routing aliases (the runtime role has no DELETE). Fixed: the schema is listed and `routing_alias` alone gets DELETE.
3. **Email fails fast.** With email on, the cell refuses to start without an SMTP username and password file. That is intended; the rehearsal supplies a placeholder for the sink, and a real deployment must supply the real ones.

Also visible in the logs: the worker repeats `sanctions list … FETCH_BLOCKED` because no list download addresses are configured (see the open list below).

## Your target: GCP virtual machines only, all stored data in PostgreSQL

Assumptions behind this section (tell me if one is wrong): the application and the database both run on Compute Engine VMs you manage (no Cloud SQL, no GKE, no Cloud Run, no managed cache), and "data" means everything the product stores about companies, people, billing, evidence and audit.

### Where each kind of data lives

| Kind | Stored in | Notes |
|---|---|---|
| Accounts, organizations, authority, mandates, credentials, verification, screening, billing, invoices, email log, events (outbox) | **PostgreSQL** | already, in the cell database (`zid_cell`) |
| Evidence ledger, checkpoints, decision receipts, audit, legal holds | **PostgreSQL** | already; objects are stored as encrypted `bytea` (`evidence.evidence_object`) |
| Company proof documents (uploads) | **PostgreSQL** | already; encrypted, scanned first. The backend has no object-storage dependency (checked: no S3 or GCS use) |
| Global locator and discovery | **PostgreSQL** (`zid_cell`, schema `global_directory`) | its own schema in the one database; no personal data |
| Signing and encryption **keys** | **Cloud KMS** (not a VM) | cannot go in the database: the code refuses local keys outside `local` and `test`, and a private key beside the data it protects defeats the design. Cloud KMS stores no data of yours, only key custody. If you want no Cloud KMS at all, that is new development (see below) |
| Customer connection **secrets** (enterprise OIDC client secrets) | **OpenBao** (runs on a VM) | production requires the OpenBao broker. By default it stores on a VM disk (`storage "file"`); it can use PostgreSQL as its storage backend instead, which puts them in the database (encrypted by OpenBao, auto-unsealed by KMS). A config change; not yet tested here |
| **Web sessions** (who is signed in) | **Valkey** (a container on the VM) | the web app refuses to start in production without it. Not business data (losing it signs everyone out), but it is the one runtime store outside the database. Moving it into PostgreSQL is new development |
| SMTP password, Stripe key, TSA trust list, attestor keys | **root-owned files on the VM** | configuration, not data; or move the passwords into OpenBao |
| Backups | **outside the database by nature** | VM disk snapshots plus WAL archive; see the plan below |

### What the VM-only topology looks like

```
Internet ─▶ app VM:  Caddy (TLS) ─▶ web ─▶ cell API ─┐        db VM:  PostgreSQL 17 (zid_cell)
                                     global API ─────┼──TLS──▶  roles: zid_owner / zid_runtime / zid_worker
                                     worker ─────────┘          standby VM (streaming replica) + snapshots
            valkey, openbao (on the app VM or its own)   ──▶   Cloud KMS (keys only)
```

What exists today for this: the app side is `deploy/gcp` (compose with Caddy, OpenBao, Valkey, web, cell, global, worker; the VM, firewall, KMS key ring and service account come from `deploy/tofu/modules/gcp-platform`). What does **not** exist yet is the database VM: the tofu module creates **Cloud SQL**, and `deploy/vm` is a single-VM demo with development keys and no TLS. A VM-hosted PostgreSQL is development, not just configuration:

1. **A database VM recipe or tofu module**: PostgreSQL 17, TLS with a server certificate (and the connection URLs' TLS parameters tested with asyncpg), `pg_hba` limited to the app VMs' private addresses, `pgaudit`, the three roles and grants from `deploy/gcp/db/*.sql` (the rehearsal runs exactly that sequence), a persistent disk sized for the ledger.
2. **Durability you now own**: a streaming standby on a second VM, scheduled disk snapshots, and WAL archiving for point-in-time recovery (pgBackRest or WAL-G writing to a snapshotted disk or a bucket; a bucket is outside the database but is the standard place for backups). Cloud SQL gave you this for free; the restore, PITR and failover acceptance rows (RES, SRE) must be proved against this setup.
3. **Patching and upgrades**: you operate the operating system and PostgreSQL minor upgrades; the platform's migration runbook assumes the owner role and a maintenance window.

### Real deployment on VMs, in order

1. **GCP project**: the service account, the HSM key ring `zid-<cell>` (every key the code asks for, plus the OpenBao unseal key), a private network and firewall, from `deploy/tofu` (leave out `google_sql_database_instance`, add the database VMs).
2. **Database VM**: install PostgreSQL, create `zid_cell`, then run the same steps `./rehearse.sh up` runs: `01_roles.sql`, the schema SQL as the owner, migrate, `02_grants.sql`. Point `ZID_OWNER_*`, `ZID_RUNTIME_*` and `ZID_WORKER_DATABASE_URL` at it.
3. **App VM**: images (built and pushed, or the signed ones from the release workflow), `.env` from `deploy/gcp/env.example`, `docker compose up -d openbao vm-identity`, OpenBao bootstrap (record the recovery keys offline, split between two people).
4. **Prove the keys**: `tools/scripts/gcp_kms_smoke.py` against the real KMS before taking traffic.
5. **Start**: `docker compose --profile direct up -d`, then DNS for `id.`, `api.` and `global.` under your domain (the domain is the passkey relying-party id).
6. **First operator**: `--profile operator-bootstrap`, then `./deploy.sh <digest>` for later releases.
7. **Prove recovery**: restore a snapshot onto a spare VM and run `partition:verify` on it. The evidence chain is only as durable as this step.

## Still open before a real launch

**Decisions only you can make for this target**

- Keep **Cloud KMS** for keys (recommended, nothing else is supported in production), or fund a different key service.
- Keep **Valkey** on the VM for web sessions (recommended: small, rebuildable, no business data), or build a PostgreSQL session store so nothing at all lives outside the database.
- Store **OpenBao** on PostgreSQL (recommended for your rule) or on its VM disk.
- Where **backups** go (a bucket is the usual answer, and it sits outside the database by definition).

**Development still needed for VM-only**

- The database VM recipe or tofu module, TLS between the app and the database, the standby, snapshots and WAL archiving (above).
- Optional: a PostgreSQL session store for the web app; OpenBao on the PostgreSQL storage backend, tested.

**Launch gates (not code)**

- **Release gate**: `deploy/release/ratification.json` is blank (NO-GO) and `release.yml` blocks promotion to production until the owners record GO.
- **Commercial sign-offs**: the price book, the `plan-throughput.yaml` multipliers (still `ENGINEERING_DEFAULT`; the cell logs a warning at start-up outside local), tax (rate table or Stripe Tax), and the decision to move `ZID_COMMERCIAL_ENFORCEMENT` from `shadow` to `enforce`. Until then plan limits are logged, not enforced.
- **Verification suppliers and feeds**: Veriff and Sumsub contracts and live runs, Creditsafe, the OFAC, UK and EU sanctions list download addresses (until set, every screening decision is `UNAVAILABLE`, so no company reaches `VERIFIED`), Legal's country rule packs.
- **Uploads**: the malware scanner (`ZID_PROOF_SCANNER_ADDRESS`, clamd on a VM); without it a deployed cell refuses proof uploads.
- **Email**: a sender domain with SPF, DKIM and DMARC and a real SMTP relay; 23 of the 160 templates are wired to events that exist; the Terms of Service text is missing.
- **Evidence timestamps**: a TSA contract and its trust file (`ZID_EVIDENCE_TSA_URL`, `ZID_EVIDENCE_TSA_TRUST_FILE`); without them checkpoints are signed and chained but not externally timestamped.
- **Agents**: a real workload attestor (for example SPIRE); without `ZID_ATTESTOR_KEYS_FILE` no workload gets a token.
- **Proof that needs a deployed environment**: 98 acceptance rows (load, chaos, zone and database failover, restore and point-in-time recovery, accessibility, protocol conformance, penetration test); none of the linked rows is marked verified yet. With a self-run database, the restore and failover rows now test your own setup rather than Cloud SQL's.

**Cannot be rehearsed on a laptop**: Cloud KMS, OpenBao's auto-unseal, TLS and DNS, and the database VM's replication. The first real proof of the keys is `gcp_kms_smoke.py` on the VM.
