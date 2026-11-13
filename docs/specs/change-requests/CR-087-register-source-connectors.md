# CR-087: Official-register connectors

Status: open (built and tested; real connectors wait for each register's contract and documented interface).

## Why
ZID-RCG-003 RCG-G02 and section 05 require certification to be a fact read from the official register, kept fresh. CR-073 lets an operator
record what a register says by hand. This change lets the platform ask a register itself, under the same rules, without trusting a connector's
own claims.

## What changes
- `verification/domain/register_sources.py`: a source (official register, trusted list, professional register) is used only when it is
  configured, enabled, names the contract or published terms that permit automated queries, is allowed in this environment and has a connector.
  A reading must come from the source's register (entry URL on its https base, its scheme). The proof hash is computed from the exact bytes
  returned. "Not listed" records nothing, so the scope goes to HOLD by the CR-073 freshness rule.
- `verification/infrastructure/register_sources.py`: configuration (`config/regulatory/register-sources.yaml`, ships with no source), the
  connector protocol, and a deterministic simulator usable only in local, test and dev.
- `verification/application/register_refresh.py`: refresh one scope: check the source, read, preserve the response, record the attestation.
- Migration `verification_0012`: `verify.register_response`, append-only, keyed by the sha256 of the body (the table checks it).

## Also built
- `GET /v1/regulatory/register-sources` and `POST /v1/regulatory/attestations:refresh` (regulatory.attestation.record, audited, announces
  `verification.certification.changed`).
- Worker step `register_refresh`: asks the register again for every connector-recorded scope older than a day, each in its own savepoint.
- Console: Suppliers > Official-register sources, with "Ask the register".

## Not in this change
- Real connectors: each needs the register's documented interface and the contract or licence recorded first.
