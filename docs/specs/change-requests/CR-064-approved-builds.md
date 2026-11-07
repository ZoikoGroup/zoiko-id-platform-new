# CR-064: Approved builds for agent blueprints (ZID-MACHINE-001 §3, §9)

Status: open (built and tested; the owners confirm the enforcement default and who may approve, see below).

## Why
A blueprint release names the artifact digest of the software it runs, and a workload token needs an attestation that the running software matches
that digest (CR-042). The digest was typed in with nothing behind it: any 64 hex characters were accepted, so the control that depends on it
(the attestor compares what runs to what was approved) approved whatever a person typed. A person who does not build software also has no way to
know a digest, so the console asked for something its users could not supply. A build is the missing record.

## What changes
- **Builds** (`agent.build_artifact`, migration `machine_0005`, tenant-scoped under RLS). A person who builds software registers its digest once,
  with where it came from (a registry reference and a provenance reference, both optional text). A second person approves it. The two-person rule is
  also a table rule: an APPROVED row must name an approver other than the registrar. A build is never deleted.
- **API** (`/v1/machine/builds`): list (`machine.agent.read`), register, `:approve`, `:revoke` (`machine.build.manage`, R3/E3, new in the action
  catalogue). Digests are stored as `sha256:<hex>`; `sha-256:` is accepted and means the same build. Audit records carry the build id, digest and
  reason code, never its label (the ledger refuses names).
- **A release needs an approved build.** `POST /v1/machine/blueprints` is refused with `BUILD_NOT_APPROVED` (409) when its artifact digest is not
  an APPROVED build. New releases are stored with the one spelling of the digest, and lookups match either spelling of what is already stored.
- **Revoking a build** revokes every draft or approved release of it, suspends every workload binding on its digest and every active agent built on
  it (reason `BUILD_REVOKED`), and records the counts. It is the supply-chain kill path for one build, as revoking a blueprint is for one release.
- **Setting** `ZID_AGENT_BUILDS` = `auto` (default) | `enforce` | `off`. `auto` enforces everywhere except `local`, `test` and `dev`, so a deployed
  environment cannot draft a release from a typed digest. `off` is refused at start-up outside local, test and dev. `GET /v1/machine/builds` says
  whether it is enforced, so the console can explain a refusal before it happens.
- **Console** (`Agents & tools` → Builds, and the blueprint dialog): a blueprint release now picks its build from the approved ones. A test build
  (a placeholder digest) is offered only in a development build of the console.

## Owner decisions (proposed values, none ratified)
1. **Enforcement default.** Proposed: required outside local/test/dev. Alternative: required only where `ZID_AGENT_BUILDS=enforce` is set.
2. **Who approves.** Proposed: any holder of `machine.build.manage` other than the registrar, as for blueprints. Alternative: a named role.
3. **Provenance.** A build records a provenance reference as text and does not verify it. Verifying a signature or SLSA attestation (for example
   with cosign) against a trusted signer is not built: it needs a decision on which signers to trust.

## Not built, and why
- Registering builds from a CI pipeline with a scoped machine credential (today a person registers a build in the console or over the API).
- Deriving the attestor's per-workload approved digests from approved builds. The attestor registration file stays the reviewed list
  (`docs/architecture/workload-attestor.md`); a later change can generate it.
- Listing images from a container registry: it needs registry access and credentials, which the platform does not hold.

## Tests
`test_agent_builds_pg.py`: nobody without authority sees or changes a build; register, duplicate (either spelling), the registrar cannot approve,
a second person can, status filters; unenforced releases still work; enforced releases refuse an unknown build and a draft build and accept an
approved one in either spelling; revoking a build revokes its releases and nothing can use it; revoking suspends the agent, binding and release
running it; the table refuses a self-approved row; another tenant sees nothing; the enforcement default by environment.

## Acceptance rows
None linked yet (the Phase H rows for the agent registry stay as they are).
