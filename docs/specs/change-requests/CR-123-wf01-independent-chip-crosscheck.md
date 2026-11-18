# CR-123: independent cross-check of our chip reader against JMRTD and NFCPassportReader

Source: Wireframe 01 (chip reader row, decisions B and C) and Wireframe 03 (CTO decision, reader row, engineering item E03). Status: engineering built 18 Nov 2026. Nothing here has touched a passport.

## Why

The wireframes name JMRTD (Android) and NFCPassportReader (iOS) as research and evaluation components, with a licensed specialist as the primary and "self-run" allowed only "if independent self-run assurance gates pass". We wrote our own reader (CR-108, CR-111) and proved it against the standard's worked examples and a simulated chip. This change builds the gate that independent evidence has to pass, and the way to feed it.

## What was built

- `evidence/domain/chip_crosscheck.py` (pure): `ChipRun` with the READ report (hash of the signed object and of each data group) kept apart from the CRYPTOGRAPHIC report (passive authentication, issuer chain, revocation, Active Authentication, chip authentication). `compare(ours, reference)`: only the same document, the same signed object and the same phone are comparable; a check one side did not run is "not comparable", never agreement; a pass where the reference failed is **critical**, the reverse is only "ours stricter". `gate(...)`: never passes without a recorded policy, needs enough distinct documents, phones, access methods and reference readers, a high enough share of passive authentication actually compared, and **no critical disagreement at all**; a pair that is not comparable adds nothing.
- `parse_run` and `pair_runs`: a strict parser for the run file (unknown keys, bad hashes and unknown verdict words refuse the whole file) and the pairing by document, phone and access method, naming what found no partner.
- `evidence/infrastructure/crosscheck_policy.py` and `backend/config/self-run-verification/chip-crosscheck-gate.yaml`: the policy, read strictly. **It ships with none**, so the gate reports `NOT_CONFIGURED`; the numbers are the owners' risk decision and none is proposed.
- `tools/scripts/chip_crosscheck.py`: the lab command. It exits 0 only when the gate passes (2 when unconfigured, 3 on a bad file) and prints a JSON report.
- `sdks/native-bridge/crosscheck/` (contract and `run.schema.json`) and `docs/qa/wf01-chip-crosscheck-plan.md` (the matrix, how a result is read, what a pass does and does not mean, who does what).
- The method certification board now lists the cross-check among what the chip checks still need.
- Tests: `test_chip_crosscheck.py` (12) and `test_chip_crosscheck_tools.py` (15), including the command end to end.

## Not built or not proved

- **No harness app exists.** The two apps that drive JMRTD and NFCPassportReader and write run files must be written by the mobile team against the libraries' current documentation; no Kotlin or Swift is included because writing their calls from memory would be a guess presented as code.
- No real run has been compared. The gate has only ever seen synthetic runs.
- JMRTD (LGPL-3.0) and NFCPassportReader (MIT) stay `LAB_ONLY` with licence unverified in the register; each harness must remain a separate lab tool, never linked into the product.
- A pass is evidence for the tested documents, phones and methods only. It is not certification, and the wireframes' other gates (trust material, DPIA, region controls, lawful basis, device security evidence) still stand.
