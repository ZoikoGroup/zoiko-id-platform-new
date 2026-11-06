# Zoiko iD on GCP virtual machines only

No Cloud KMS, no Cloud SQL, no managed service: Compute Engine VMs running Docker and PostgreSQL. Keys live in PostgreSQL, wrapped by a master key file kept outside it ([CR-060](../../docs/specs/change-requests/CR-060-software-keys-in-the-database.md), standard assurance); secrets live in OpenBao, which stores in PostgreSQL too. Read [what that does and does not protect](../../docs/runbooks/software-keys.md) before you start, and get the Security Lead's written acceptance of CR-060: the stack will not start without it.

This was rehearsed end to end on one machine in `deploy/local-prod/` (the same images, roles, services and settings). What it cannot rehearse is marked **[first run on the VMs]** below.

```
Internet ─443─▶ APP VM:      Caddy ─▶ web ─▶ cell API ─┐                 DATABASE VM:  PostgreSQL 17
                             global API ───────────────┼──TLS 5432──▶   zid_cell  zid_global  openbao  spicedb
                             worker, outbox-relay, consumer, valkey     roles: owner / runtime / worker / relay
                                          │ 8200 9092 8443                     ▲ replication
                                          ▼                                    │
                          SERVICES VM:  OpenBao (sealed by people) · Redpanda (Kafka) · SpiceDB    STANDBY VM: streaming replica
```
Small deployments may put the services on the app VM (`-f docker-compose.yml -f docker-compose.services.yml`, `ZID_SERVICES_HOST` = the app VM's private address). The database is always its own VM.

## What is where

| Stored | Where | Protected by |
|---|---|---|
| Every business record, the evidence ledger, proof documents (encrypted), billing, audit | PostgreSQL (`zid_cell`) | row-level security, encryption of documents and fields with keys below, TLS in transit |
| All keys (signing, data, erasure, handles) | PostgreSQL, table `keys.key_version` | wrapped by the **master key file** (`secrets/master_key` on the app VM), append-only trigger |
| Customer connection secrets | OpenBao, stored in PostgreSQL (`openbao`) | sealed with Shamir shares held by people |
| Authorization relationships | SpiceDB, stored in PostgreSQL (`spicedb`) | network rules, a preshared key |
| Events in flight | Kafka (Redpanda) log on the services VM | network rules; the durable record is the outbox in PostgreSQL |
| Web sessions | Valkey on the app VM | password, network rules; losing it only signs people out |
| Master key, OpenBao token, SMTP password | root-owned files in `secrets/` on the app VM | file mode 400, owner 10001; the master key also offline with two people |

## 1. Create the VMs and the network (once)

Names, zone and sizes are examples; the principle is private addresses only for everything except Caddy.

```bash
PROJECT=zoiko-prod ZONE=europe-west2-a
gcloud compute networks create zid --subnet-mode=custom
gcloud compute networks subnets create zid-eu --network=zid --range=10.0.0.0/24 --region=europe-west2
gcloud compute firewall-rules create zid-internal --network=zid --allow=tcp:5432,tcp:8200,tcp:9092,tcp:8443 --source-tags=zid-app,zid-services --target-tags=zid-db,zid-services
gcloud compute firewall-rules create zid-web --network=zid --allow=tcp:80,tcp:443 --target-tags=zid-app
gcloud compute firewall-rules create zid-ssh-iap --network=zid --allow=tcp:22 --source-ranges=35.235.240.0/20   # SSH only through IAP

gcloud compute instances create zid-db  --zone=$ZONE --machine-type=n2-standard-4 --network=zid --subnet=zid-eu --no-address --tags=zid-db \
  --image-family=debian-12 --image-project=debian-cloud --boot-disk-size=30GB \
  --create-disk=name=zid-db-data,size=200GB,type=pd-ssd,auto-delete=no --create-disk=name=zid-db-wal,size=50GB,type=pd-ssd,auto-delete=no
gcloud compute instances create zid-svc --zone=$ZONE --machine-type=e2-standard-4 --network=zid --subnet=zid-eu --no-address --tags=zid-services \
  --image-family=debian-12 --image-project=debian-cloud --boot-disk-size=50GB
gcloud compute instances create zid-app --zone=$ZONE --machine-type=e2-standard-4 --network=zid --subnet=zid-eu --tags=zid-app \
  --image-family=debian-12 --image-project=debian-cloud --boot-disk-size=50GB            # the only VM with a public address
```
Mount the data disk at PostgreSQL's data directory and the WAL disk at the WAL archive path before step 3. Schedule disk snapshots for both (`gcloud compute resource-policies create snapshot-schedule`, daily, 14 days or longer) and point DNS `id.`, `api.` and `global.<domain>` at the app VM's public address. Install Docker on the app and services VMs.

## 2. Images

The release workflow builds, scans, attests and signs `zoiko-id-platform` and `zoiko-id-web` by digest. `docker login ghcr.io` on the app VM with a read-only token, then put the digests in `.env` (`deploy.sh` does it and verifies the signatures).

## 3. The database VM **[first run on the VMs]**

On `zid-db`, as root, from a copy of this folder:
```bash
sudo ./db-vm-setup.sh --listen 10.0.0.3 --allow 10.0.0.0/24 --wal-archive /mnt/wal
```
It installs PostgreSQL 17 with pgaudit, makes a private CA and server certificate, turns on TLS (1.2 or later), scram passwords and WAL archiving, and allows only the private network. It was written from the PostgreSQL documentation and has not run on a real VM: read what it prints and keep the `.orig` backups. Set the admin password: `sudo -u postgres psql -c "ALTER USER postgres PASSWORD '...'"`.

## 4. Roles, databases, schemas

From the app VM (needs the `psql` client), with the passwords in the environment (never in a file):
```bash
export ZID_DB_HOST=10.0.0.3 PG_ADMIN_PASSWORD=... ZID_OWNER_PASSWORD=... ZID_RUNTIME_PASSWORD=... ZID_WORKER_PASSWORD=... \
       ZID_RELAY_PASSWORD=... ZID_OPENBAO_DB_PASSWORD=... ZID_SPICEDB_DB_PASSWORD=...
./db-init.sh pre
```
The same passwords go into the connection URLs in `.env`.

## 5. The master key, then the first start of the app side

```bash
cp env.example .env && chmod 600 .env        # fill in: domain, addresses, image digests, URLs, Valkey and SpiceDB passwords, SMTP
./keys.sh init-master                        # creates secrets/master_key. Copy it OFFLINE now, split between two people.
printf %s 'the-smtp-password' > secrets/smtp_password && chown 10001:10001 secrets/smtp_password && chmod 400 secrets/smtp_password
```
Put the Security Lead's acceptance in `.env`: `ZID_KEY_PROVIDER_DB_ACK=accept-software-keys` (only once CR-060 is signed).

## 6. The services VM, and OpenBao

On `zid-svc`: copy `docker-compose.services.yml` and a `.env` with `ZID_DB_HOST`, `ZID_SERVICES_HOST`, `ZID_OPENBAO_DB_PASSWORD`, `ZID_SPICEDB_DB_PASSWORD`, `ZID_SPICEDB_PRESHARED_KEY`.
```bash
docker compose -f docker-compose.services.yml up -d
./openbao.sh init                  # prints 5 unseal shares and the root token ONCE: give each share to a different custodian
./openbao.sh unseal                # three custodians enter their shares (hidden input)
./openbao.sh app-token ./app_token # issues the platform's token; copy it to the app VM as secrets/openbao_token (owner 10001, mode 400), then delete it
```
**Every restart of OpenBao starts sealed** and the platform cannot read customer connection secrets until three custodians run `./openbao.sh unseal`. Plan for that (an on-call rota, a restart procedure); it is the price of not having a KMS to unseal it.

## 7. Migrate, privileges, start

On the app VM:
```bash
docker compose run --rm migrate            # tables, as the owner role
./db-init.sh post                          # privileges for the runtime/worker/relay roles (from a machine that reaches the DB)
docker compose run --rm cell python -m zid_platform.entrypoints.seed_registration_tenant --apply    # prints ZID_REGISTRATION_TENANT_ID: add it to .env
docker compose --profile edge --profile events up -d
```
Check: `curl https://api.<domain>/health/ready`, `https://global.<domain>/health/ready`, `https://id.<domain>`. Then `./keys.sh status` lists the keys in the database; `./keys.sh public` prints the public keys.

## 8. First operator

Set the `ZID_OPERATOR_*` values for the operator account (see `deploy/gcp/README.md`, "operator-bootstrap"): it creates the first operator and prints one enrollment link.

## Day two

- **Release:** `./deploy.sh sha256:<backend> sha256:<web>` verifies signatures, migrates, restarts, waits for readiness. After a release that adds a schema, run `./db-init.sh post`.
- **Back up:** disk snapshots of the data and WAL disks (scheduled in step 1) plus the master key offline. A database backup restored without the master key is unreadable by design.
- **Prove recovery before launch:** restore the latest snapshots onto a spare VM, start PostgreSQL, run the cell against it with the master key, and call `POST /v1/evidence/partition:verify`. The evidence chain is only as durable as this step.
- **Standby:** on a second VM, `pg_basebackup -h 10.0.0.3 -U replicator -D <data> -R -X stream`, then promote on failure. Create the `replicator` role with a password first. **[first run on the VMs]**
- **Rotate keys:** `./keys.sh rotate-signing --key-id ...`; master key rotation is in the [runbook](../../docs/runbooks/software-keys.md).
- **Rotate the database CA/certificate** before it lapses (2 years).

## Evidence integrity and alerts

The worker re-verifies the evidence ledger every 6 hours (`ZID_EVIDENCE_VERIFY_INTERVAL`) and raises the `evidence.integrity.failed` incident (an email to the SECURITY role) if anything fails; with software keys a timestamp authority is mandatory unless the owners waive it in writing (`ZID_EVIDENCE_TSA_WAIVER`). Point Prometheus at `worker:9102/metrics` over the private network and load `deploy/observability/prometheus/rules/zid-evidence.yaml`.

## Settings you will want soon

`ZID_EVIDENCE_TSA_URL` with `ZID_EVIDENCE_TSA_TRUST_FILE` (an external timestamp on every evidence checkpoint: with software keys this is what makes a rewritten history detectable); `ZID_PROOF_SCANNER_ADDRESS` (a clamd VM; without it proof uploads are refused); `ZID_COMMERCIAL_ENFORCEMENT=enforce` once Commercial has signed.

## Known limits of this setup

- Software keys are not FIPS-validated and are exposed by a compromise of the app VM (CR-060).
- The Shared Signals transmitter (security event push to relying parties) runs on the same `set` key ring the API publishes. An independent witness copy of the evidence checkpoints does not exist yet: until it does, the external timestamp authority is the only thing outside your control that vouches for history.
- One cell, one region. A second cell means a second copy of this folder and `ZID_LOCATOR_CELL_KEYS` on the global plane.
