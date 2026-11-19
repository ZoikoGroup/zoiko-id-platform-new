# Owner pack: the face-match and liveness engine (CR-095, CR-102, CR-107)

A checklist for the owners. It approves nothing, and every reference below is blank on purpose. It exists because the platform's own readiness check (`GET /verification-engine-readiness`, or `readiness()` in `verification/domain/biometric_engine.py`) reports **DISABLED, not approvable, 15 problems**, and each problem is a record only a person can make.

The flow this unlocks: a person uploads a passport or an identity card, then takes a live capture, the face is compared with the document photograph and a liveness test runs, and a pass on both approves the check without a reviewer. The platform refuses to run any face engine until every item below is recorded in `backend/config/self-run-verification/self-run.yaml` and `engine-register.yaml`. Filling a field in does not make the engine good: the evaluation behind it has to be real.

## 1. What blocks approval today, and who closes it

| # | Problem the platform reports | What closes it | Owner | Reference |
|---|---|---|---|---|
| 1 | `ENGINE_ID_MISSING`, `ENGINE_VERSION_MISSING` | Name the adapter (`verification/infrastructure/engine_adapters.py`) and the exact model version it runs | Engineering with Security | |
| 2 | `ENGINE_COMPONENTS_MISSING` | List the register ids of every library and pretrained model the engine is built from (for example `deepface`, `silent-face-anti-spoofing`) | Engineering | |
| 3 | `LICENCE_CHECK_NOT_RECORDED` | A person reads the licence of the engine **and of each pretrained model**, then sets `licence_checked: true`. In `engine-register.yaml` each component also needs `commercial_use: YES`, `model_licence_confirmed: true` and `licence_verified_on` | Legal | |
| 4 | `EVALUATION_ID_MISSING`, `EVALUATION_DATE_INVALID` | An **independent** accuracy and bias evaluation of this exact engine and version, with its id and date. It expires after 365 days | Security with an independent evaluator | |
| 5 | `MEASURED_FALSE_ACCEPT_MISSING`, `MEASURED_FALSE_REJECT_MISSING` | The measured rates from that evaluation. Ceilings: false accept at most 0.001, false reject at most 0.05 | The evaluator | |
| 6 | `PAD_EVALUATION_STANDARD_NOT_RECORDED`, `MEASURED_ATTACK_ACCEPT_MISSING`, `MEASURED_BONA_FIDE_REJECT_MISSING` | A presentation-attack test of the liveness check by an independent lab (ISO/IEC 30107-3), with the measured attack accept (at most 0.05) and genuine reject (at most 0.10) rates | The lab | |
| 7 | `FACE_THRESHOLD_INVALID`, `LIVENESS_THRESHOLD_INVALID` | The score a face match and a liveness result must reach, chosen from the evaluation (between 0.5 and 1) | Security | |
| 8 | `DPIA_REFERENCE_MISSING` | The reference of the approved data protection impact assessment | Privacy | |
| 9 | `ENGINE_SCOPE_MISSING` | The countries the engine is approved for (an evaluation covers a population, not the world). Mode `APPROVED_FOR_SCOPE`; `LAB_ONLY` never decides a live case | Product with Legal | |

Per country, a live session also needs a published biometric notice and the person's consent (`privacy-biometric-owner-pack.md`), and each biometric asset class needs a retention rule. Until then the identity page says "No notice has been published for this country yet" and face checks stay off.

## 2. What engineering can do without these records

- Run a candidate engine on **photographs you supply**, in `LAB_ONLY`, to measure its scores. That output is a starting point for the evaluation, not the evaluation: it is not independent and it never approves a live case.
- Build the live-capture step so the page and API guide the person through document, then live face, then result, staying behind the gate.
- Keep the readiness check honest: it lists exactly the problems above and changes nothing.

## 3. What nobody should do

- Enter an evaluation id, a lab reference, an impact assessment reference or a licence confirmation that does not exist. The platform cannot tell, which is why the record is a person's signature on a real piece of work.
- Treat a face match alone as proof of identity: it can be shown another person's photograph. The liveness evaluation (item 6) is what stops that.

## 4. After the records exist

Set the values in `self-run.yaml`, add the components to `engine-register.yaml`, restart nothing (both files are read on each call), and open **Regulatory → Biometric methods** in the Operator Console to move the method scope up a state: one person proposes and another approves each step, and "approval is not activation" until the customer's own impact assessment and the country overlay are in place.

Related: [privacy-biometric-owner-pack.md](privacy-biometric-owner-pack.md), [identity-suppliers-owner-pack.md](identity-suppliers-owner-pack.md), [CR-095](../specs/change-requests/CR-095-self-run-identity-verification.md).
