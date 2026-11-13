# CR-072: Market releases: seven evidence gates, four independent approvals (ZID-RCG-003 RCG-G10, WF-08)

Status: open (domain, storage, lifecycle service, operator API, audit and the country gate built and tested; no console yet; the owners confirm the points below).

## Why
An ENABLED country and an approved policy pack (CR-067) say what the rules are. Neither says that anyone checked the market is ready: legal
position, supplier status, evidence integrity, privacy and security, quality, continuity and contract assurance. Document 03 asks for that as
seven gates with named owners, then four independent approvals of one exact release, before a market opens.

## What changes
- **Three tables** in the `verify` schema (migration `verification_0006`), registered in `backend/database/table-ownership.yaml`:
  `verify.market_release` (one row per release version: market, status `DRAFT`, `FROZEN`, `RELEASEABLE`, `LAUNCHED`, `WITHDRAWN`, author, pinned
  packs and supplier scopes, release hash), `verify.market_release_gate` (every gate result, append-only) and `verify.market_release_approval`
  (append-only). Platform configuration: no `tenant_id`, no row-level security, no DELETE grant. At most one LAUNCHED release per market.
- **Table rules** that hold when the domain code is skipped: a frozen release never changes; freezing needs seven PASSED gates and a pinned pack;
  evidence is draft-only and never from the author; an approval signs the stored hash, is not the author's or an evidence recorder's, and needs a
  FROZEN release; RELEASEABLE needs four approvals.
- **Domain** (`domain/market_release.py`, pure): gates RG-01 to RG-07, each owned by one role; `freeze` pins the approved packs in force and the
  supplier certifications the author names (each must be CERTIFIED on fresh evidence, CR-073); four approver roles (legal, security and GRC, QA,
  product owner), one person one role; `launch`, `withdraw`, `revise` (a new draft, nothing carried over); `market_decision`, which fails closed:
  no release, not launched, withdrawn, a pack changed after the release, a pinned supplier on hold.
- **Service** (`application/market_releases.py`) and **store** (`infrastructure/market_release_store.py`, which re-hashes a stored release on read).
- **Operator API** (`operator_access/api/regulatory_releases.py`): list, get, `market-status`, open, `:revise`, `:attest`, `:freeze`, `:approve`,
  `:launch`, `:withdraw`. Capabilities: `regulatory.release.author`, six `.attest_<role>`, four `.approve_<role>` (two approvers), and
  `.launch` (two approvers). One separation-of-duties set (`regulatory-release-maker-vs-checker`): the author, each gate role and each approver
  role are held by different people, and one person holds one of them.
- **The gate** (`country_gate.check_packs`, `release_mode`). After the pack check, an ENABLED capability also needs `market_open` for its market, or
  it is refused with `MARKET_NOT_RELEASED` (shadow only logs). Switch `ZID_MARKET_RELEASES`: `shadow` (default), `enforce`, `off` (refused outside
  local, test and dev). A release that cannot be read counts as not released. Same entry points as CR-067.
- **Audit.** `regulatory.release.opened`, `.revised`, `.gate_attested`, `.frozen`, `.approved`, `.launched`, `.withdrawn` in the same transaction
  as the change; a refusal is recorded afterwards as `regulatory.release.refused` with its reason code. Ids, codes, roles and hashes only.

## Owner decisions (proposed values, none ratified)
1. Gate owners and approver roles as in the document; who holds each capability is a business decision.
2. Security owns two gates (RG-03, RG-06); proposed: the same person may attest both.
3. Default `shadow` until the first real market is released; then `enforce`.
4. Operator routes use `/v1/regulatory/...`, not `/v2/...`.
