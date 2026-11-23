# Agent release plan: what each phase has to prove, and who closes each open item

Status: **PROPOSED by engineering, for the Product owner to adopt, change or reject.** Nothing here is approved. Written 21 Nov 2026 from ZID-AG-WF-001 (the release sequence and gates G0 to G4), ZID-AG-WF-002 (gates G0 to G5), the owner decision pack, and what is built (CR-141 to CR-145).

A list of open gaps reads as "not ready". A release plan reads them as what each phase has to prove. The design already says how an agent is released: **Phase 0** synthetic records, **Phase 1** internal dogfood in an isolated tenant, **Phase 2** one consenting design-partner tenant after privacy, policy, security and support sign-off, **Phase 3** broader release only after independent evidence. This puts every open item in the phase where it starts to matter, with the risk the Product owner accepts until then, the owner who closes it, and the evidence that closes it.

## Where we are

| Phase | State |
|---|---|
| 0, synthetic | **Done.** 103 acceptance rows pass in engineering's own run (`docs/signoff/runs/`), 6,374 unit and contract tests, 124 component tests, and a restore drill (`docs/runbooks/drills/`). Not independent. |
| 1, internal dogfood | **Ready to start, not started.** The stack runs from production images; TESCO PLC is the isolated tenant; there are no mandates, baseline, policy or runs yet. |
| 2, one design partner | Blocked on the items marked P2 below. |
| 3, broader release | Blocked on the items marked P3 below, and on the commercial release register, which is a separate instrument. |

## The open items, placed

| # | Open item | Needed for | Why that phase | Closes it | Owner | Evidence |
|---|---|---|---|---|---|---|
| 1 | The commercial release is NO-GO (`deploy/release/ratification.json`: ratifications, gates G0 to G6, seven signatories) | **P3** | That register covers money: prices, billing rights, discounts, the finance console. A pilot that charges nothing does not touch it. **Decision for the Product owner: P1 and P2 are free pilots.** | The owners complete the register | Founder, CFO, CCO, CTO, Legal | Signed register; `tools/scripts/go_live_board.py` shows it |
| 2 | No independent acceptance run | **P2 for gates G1 and G3, P3 for all rows** | WF-001: "broader release only after independent evidence". A design partner should not be the first outsider to run it. | Engage a tester outside engineering; they run `tools/scripts/acceptance_run.py ... --executed-by "<name>"` on a clean checkout and compare `rows_hash` | Product owner appoints; QA lead | Their record next to engineering's, every difference explained |
| 3 | Security owner has not answered section C of the decision pack | **P2** (P1 runs on the rehearsal stack, where API agents are on) | The rules extend what a key alone may do (CR-094, CR-142, CR-144). Internal dogfood on a rehearsal stack, with no customer data, is where to learn whether they are right. | The security owner answers each row; production sets `ZID_API_AGENTS=on` only then | Security owner | Signed section C; the two sign-off packs for CR-093 and CR-094 |
| 4a | HTTPS edge, domain, passkey relying-party id | **P2** | A partner signs in from outside. Localhost is fine inside. | `deploy/gcp-vm` or `deploy/gcp` first run | Platform | `RB-` runbooks followed; certificate issued |
| 4b | Timestamp authority is the rehearsal's own | **P2** | Without an outside witness a rewritten history cannot be told from the original. | Choose and contract an authority; put its root in `ZID_EVIDENCE_TSA_TRUST_FILE`; run `tools/scripts/tsa_check.py` against it | Security + Product | `tsa_check.py` passes against the contracted authority |
| 4c | Key custody is software keys under a master key file | **P2** (the owner's written acceptance, CR-060) or KMS | A deliberate exception to DEC-F5 that needs the Security / Cryptography Lead's written acceptance in a deployed environment | Accept in writing, or use Cloud KMS | Security / Cryptography Lead | The signed acceptance |
| 4d | Backups, restore and monitoring on real machines | **P2** | A partner's data must be recoverable. **Partly closed:** a restore drill passed on the rehearsal database on 21 Nov 2026 (`docs/runbooks/drills/restore-drill-2026-11-21.md`): every counted table matches, migrations and schema identical, and all 6 evidence partitions verify on the restored copy (1,533 entries, 228 checkpoints, 228 timestamp tokens). It is one machine and one point in time. | Repeat `tools/scripts/restore_drill.sh` on the real deployment, restore on a different machine from the backup and the key custodians' copy, and wire the evidence alerts (`deploy/observability`) | Platform / SRE | A dated drill record on the real deployment; the alerts firing in a test |
| 4e | Attestor and SPIRE have never run | **P3, and only if a container agent is offered** | API-only agents do not use them; they inspect images. | First run on the VMs per `deploy/gcp-vm/README.md` | Platform | Attestation records for a real container |
| 4f | The load test at the gate rate was never run | **P3** | A pilot is small. A smoke load at pilot scale (a few concurrent agents) is enough for P2. | A performance environment and load generators | SRE | `tests/load/machine-token` result against `contracts/baseline/slo.yaml` |
| 5 | Never run on real data | **P1 itself** | This is what P1 is. | TESCO's owner and a second person do the steps in "Phase 1 checklist" below | Product owner + TESCO owner | The dogfood record |
| 6a | No screen-reader pass | **P3** (P2: one person's keyboard-only pass, about a day) | Automated checks run; a real device is a person | NVDA with a browser and VoiceOver with Safari, per `docs/qa/agent-independent-test-and-a11y-protocol.md` | Accessibility tester | One recorded script per page |
| 6b | DAARA runs in the worker, not as a separate attested service | **P3, decided then** | Today every run is least-privilege already: its own lease, a live proof at every step, and the database role row-level security applies to (CR-142). A separate service adds a second trust boundary. | Decision D-08 | CTO | The decision, then a deploy of the same code as its own service |

## Decisions asked of the Product owner

Each has a recommendation. Accepting is "yes, as written".

1. **Adopt this phasing.** Recommended yes. It is the design's own, not a new bar.
2. **P1 and P2 are free pilots; the commercial register gates P3 and general availability only.** Recommended yes. It keeps the agent from waiting on pricing.
3. **Accept for Phase 1 only** the rehearsal-grade items 4a to 4f, because P1 uses the rehearsal stack on one machine, with synthetic and internal records only, and no customer data. Recommended yes, with the condition that **no customer personal data enters the stack** until P2 gates pass.
4. **Appoint the independent tester** (item 2) now, so the run is scheduled before P2 rather than after.
5. **Choose the timestamp authority and key custody** (items 4b, 4c) now: they have the longest lead time (a contract, a written acceptance).
6. **Defer D-08 to P3** and keep the worker as built: it is least-privilege today. Recommended yes.
7. **Name the design partner and the support contact** for P2 (WF-001 G4: "design-partner consent and support escalation").

## Phase 1 checklist (what "run on real data" means, with its own exit evidence)

By TESCO's owner and a second person, using `docs/operator/agent-governance-guide.md`:

1. Create the two read tools, the API client (its public key from `agent.pub` or the browser), the `daara` release, the agent; bind, attest, activate with someone outside the chain.
2. Create at least these mandates on an evidenced grant (`01a1f7a5...` or `01a1f94e...`): healthy (90 days), expiring (about 5 days), ending in minutes, and one on the onboarding grant (expect "evidence reference missing").
3. Set the company baseline and the two signers; set up the agent, validate, dry run, publish with two independent signers.
4. Run it. **Expected:** one proposal per finding, masked targets, "No access permissions changed", 0 privileges changed; a second run with the same data proposes nothing new.
5. Pause the agent, and confirm the next run is refused (the emergency stop); restore it with two people.
6. Revoke the API binding and confirm the next run is refused (`AGENT_NOT_ATTESTED`).
7. Export the evidence and check it verifies.

**P1 exit:** every expected result above, recorded with dates and names, and no unexplained difference between what was entered and what the agent proposed. Engineering will compare the proposals in the database with the mandates entered, read-only, on request.

## Phase 2 entry (one design partner)

Items marked **P2** closed with their evidence; the Product owner's decisions 4, 5 and 7 made; privacy, policy, security and support sign-off (WF-001 gate G2 and the G1 security gate); the partner's written consent. The partner's data goes only to a deployment that has passed items 4a to 4d.

## Phase 3 entry (broader release)

Items marked **P3** closed; the independent run complete with every difference explained; the commercial register completed; D-08 decided; the screen-reader pass done.

## What engineering does, in order

1. Support Phase 1 (compare results, fix what it finds).
2. Prepare the independent tester's pack (done: `docs/signoff/uat-independent-execution-pack.md`, `tools/scripts/acceptance_run.py`).
3. Prepare the first VM deployment against the P2 items (the deploy scripts exist; the first run needs the machines and the domain).
4. Nothing is marked approved, ratified or certified by engineering. A phase opens when its named people sign.
