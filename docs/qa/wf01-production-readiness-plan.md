# WF01 identity verification: what "production ready" means here, and the plan to get there

Prepared 2026-11-17 by engineering for the product owner. It sequences work; it commits to no dates and approves nothing. Companion to `wf01-identity-verification-matrix.md` (what exists) and CR-107 / CR-108 (how it works).

## 1. The definition

"Production ready" for identity verification is not "the code works". It is four things, in this order, for one market and one method at a time:

1. **Engineering complete (E2).** Built, fails closed, tested. The software parts below are here.
2. **Proved on real things (E3).** Real passports on real phones; a real engine measured by an independent lab; staging end to end with negative cases. Nothing here has been done.
3. **Lawful and approved (G0).** DPIA, lawful basis, regions, retention, who may approve what. The platform refuses to run an engine or a country list without the record; it cannot make the record.
4. **Operated.** Monitoring, runbooks, incident handling, an owner on call.

Code can finish (1) completely and prepare (2) to (4). It cannot finish them, and any claim of "verified", "approved" or "ready" without them would be exactly the false assurance the platform exists to prevent.

## 2. Closed in this work

| Gap | How it is closed | Evidence |
|---|---|---|
| The relay kept link state in one process, so several processes needed sticky routing | **Stateless.** The protocol state travels in a sealed token (AES-256-GCM under the tenant's key, the project's own envelope): any process takes any step; nothing to clean up; tokens are bound to company, person, client and session and expire | `test_sealed.py`, `test_chip_link.py` (including taking steps in a replaced process), `test_chip_relay_pg.py` |
| No way to read a chip without someone else's app | Our own reader (CR-108) and relay, with a TypeScript device loop | ICAO appendix D vectors, simulated chip, relay tests, `chip-relay.test.ts` |
| Active Authentication depended on the app | The server runs it through the relay with the session's own challenge | relay clone test |

Two properties to know, not gaps: a token does not survive a **local development** restart (development keys live in memory; a deployed key service or a shared keystore does), and the sealed state carries the document details and session keys, so it is a secret that only ever leaves the process sealed.

## 3. Open items: decision, owner, acceptance evidence

Roles are proposed accountable functions, not appointments. "Done" means the evidence exists and is linked in the acceptance register.

| # | Item | What a product owner decides | Who does it | Done when (evidence) | Needs from us / you |
|---|---|---|---|---|---|
| 1 | **PACE** (modern passports) | Whether any launch market needs the one PACE variant not implemented (the chip-authentication mapping) or explicit domain parameters | Identity engineering | **Built and proved against the standard**: ECDH and DH, generic and integrated mapping, AES-128/192/256, 3DES (generic mapping), the CAN and the MRZ as password, eleven curves and three DH groups. Appendices G.1, G.2, H.1 and H.2 are reproduced value by value through the tokens (CR-108). **Still open**: a real-chip run; the AES secure channel and the 3DES-PACE path (the standard prints no vector); AES-192/256 integrated mapping (no printed example); the 2048-bit DH groups and the three extra Brainpool curves are proved by arithmetic, not by a printed vector | Real passports that offer PACE (and one with a CAN); the passport mix per market |
| 2 | **Real-passport and real-phone test** | Which countries' passports and which phones define "supported" (the device matrix) | Mobile lead + QA | A signed test run: each phone × passport result, failures with causes, recorded in the UAT register (`uat-cases.csv`) | Physical test passports (your own, with consent) and phones; no counterfeits |
| 3 | **Native bridge shells (Android, iOS)** | Staff or contract the build; who owns store releases | Mobile lead | Both apps build, read a passport end to end through the relay, pass the device matrix | A developer, an Apple developer account and NFC entitlement, an Android signing key. Reference source: `sdks/native-bridge/README.md` |
| 4 | **Selfie and face match** | Whether the first launch needs a face at all. Recommendation: **no**. Launch chip-only (document integrity, `VERIFIED_LIMITED`) and add face when an engine is approved | Product + Identity risk | Engine mode `APPROVED_FOR_SCOPE` per country with a complete evaluation record; then a capture screen is built behind the same gate | Nothing to build first: a camera screen with no approved engine would only collect data to ignore |
| 5 | **Lab evaluation** (PAD, face accuracy, fairness) | Fund it (budget), choose accredited lab, choose the countries and the population | Security + Finance | A signed lab report (ISO/IEC 30107-3 PAD; accuracy and bias on a representative population) entered in `self-run.yaml` with measured rates inside the ceilings | Money and a lab. The harness (`biometric_evaluation.py`) computes bounds from the lab's trials |
| 6 | **DPIA and lawful basis** | Approve the draft per market; decide retention | DPO + Legal | Signed DPIA reference in the engine record; retention schedule signed (decision H) | A draft exists in `docs/privacy`; counsel signs |
| 7 | **Licence readings** | Who reads each licence | Legal | Each register component has a verification date, commercial use confirmed, model licences read (`engine-register.yaml`) | Reading time. The register tells them exactly what to read |
| 8 | **Budgets** | Each company's monthly supplier-spend limit and currency | Finance | `PUT /verification-budget` set per company | Numbers. None are invented |
| 9 | **Real country lists** | Which countries to trust, custodian and approver | Security PKI owner + Legal | Master lists staged, validated, approved by a second person and activated in the trust store (CR-107) | Each country's own master list and anchor fingerprint |
| 10 | **Sign-offs and gates** | Approve architecture (G0), lab/SDK (G1), pilot (G2), market GA (G3) | CTO + Security + Privacy + Compliance + QA | Signed records on the go-live board | People |

## 4. Recommended order (a sequence, not a schedule)

1. **Decide the launch profile.** Recommended first launch: **document integrity by chip, no face, markets whose passports support BAC, relay on our own reader**. This needs no lab, no engine and no biometric DPIA. Everything biometric waits.
2. ~~Obtain the ICAO PDF and build PACE~~ **done** (CR-108); run it against real passports that offer PACE, the integrated mapping, DH groups, 3DES and the CAN are built too; the chip-authentication mapping is added only if a launch market's passports need it.
3. **Staff the two shells and build the device matrix** in parallel with 2; run it against real passports.
4. **Import and approve the first countries' lists** (two people).
5. **Staging pilot** with synthetic and consented real documents (gate G2): negative cases, provider and trust-store rollback, appeals, runbooks.
6. **Market GA** (gate G3), one market at a time.
7. **Face and selfie** later, as its own release, after items 4 to 7 above for that market.

## 5. Stop-ship conditions

Do not enable a market if any of these is true: no approved trust list for that country (every chip would be indeterminate); no device matrix covering the supported phones; a market whose passports use a PACE variant not implemented (the refusal names it); any biometric route without an evaluation and DPIA; no named custodian and approver; no on-call owner.

## 6. What I need from you now

1. Confirmation of the **first launch profile** in section 4 (chip-only, no face). It removes the largest dependency chain.
2. Names for **custodian, approver, mobile owner and Legal reader** (roles only are enough to start the records).
3. **Real passports** (your own, with consent) that offer PACE and ones that only offer Basic Access Control, with the phones to read them, so the first device run can happen.
