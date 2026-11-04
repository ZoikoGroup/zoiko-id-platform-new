# CR-055: Release ratification register and the go-live gate (ZID-COM-RAT-011, Document 11), slice L-15

Status: open (built). The document is an unsigned approval instrument; its default is NO-GO and this change keeps it that way.

## What changes
- **The register** (`deploy/release/ratification.json`) is the owners' instrument, shipped blank: the ratification register CL-001 to CL-006 (Approve, Amend or
  Defer, each with a decision record, a revised policy version, an owner and an effective date), the gates G0 to G6 (each needs a named reviewer, an immutable
  evidence reference and PASS), the evidence register E01 to E07, the release targeting fields, the seven signatories (Founder, CFO, CCO, CTO, Security and Privacy,
  Finance Controller, Legal), the five no-go conditions, and the policy flags. The recorded disposition is `NO-GO`. Engineering fills in none of an owner's decisions.
- **The checker** (`tools/scripts/ratification.py`, standard library only), fail-closed:
  - `--check` (run in CI): the register is well formed and claims nothing it cannot back. A GO the register does not support, a CONDITIONAL GO over an open P0 no-go, a gate
    passed without a named reviewer or an evidence reference that resolves, an Amend or Defer without its record, a Founder and a CFO who are one person, and a policy flag
    that drifted from the register are all refused.
  - `--require-go` (run before production promotion): exits 1 unless the recorded disposition is GO, the register supports it, and every no-go condition is closed.
  - `--report`: writes the checklist for the signatories (`docs/signoff/release-ratification.md`). It decides nothing.
- **No-go conditions close only on checks, never on a say-so.** A condition closes when the ratifications it names are Approved, its gates are PASS, its acceptance rows are
  VERIFIED (not merely in progress), its CI jobs exist and are required by the `checks` gate, and (for the monetary one) the finance policy lock still skips only `local` and `test`.
- **Policy flags default disabled.** A flag whose closure is not yet Approved must still be in a disabled value (`commitment.enforcement` OFF or REPORT_ONLY; `band_matrix_enforcement`
  REPORT_ONLY), and a flag's shipped value is recorded in the register, so changing the file needs a register change that is reviewed. The money flag `bill_on_commit` must stay false
  on every meter.
- **Wiring.** `ci.yml` (devsecops-controls job) runs the register check and `test_ratification.py`; `release.yml` runs `--require-go` in the production promotion, before the
  supply-chain gate (`release_gate.py`). Production promotion therefore needs the owners' recorded GO: until the register is completed and signed, promotion is refused. This is the
  intended effect of the document; to promote something that has nothing to do with commercial go-live, the owners must either record the disposition or amend the workflow in a reviewed change.

## Not changed
No runtime behaviour, no API, no migration. The register cannot be edited by any service.

## Not mine
Gates G0 to G6, the evidence E01 to E07 and the seven signatures are the owners', and so is the GO, CONDITIONAL GO or NO-GO. The titles of E01 to E07 are not in the supplied
documents and are left as identifiers.

## Tests
`tests/unit/test_ratification.py` (30): the shipped register is consistent and NO-GO; every refusal above; GO is reachable only on a tree where everything holds and one missing
piece returns it to NO-GO; a no-go stays open for an in-progress row or a CI job outside the gate; the wiring in `ci.yml` and `release.yml`.
