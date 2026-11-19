# CR-130: calibrating the face threshold from measurements, and the road to a gated face engine

Source: owner request, 19 Nov 2026, following the recommended stack: detection with SCRFD or YuNet, comparison benchmarked across EdgeFace, AdaFace and MagFace, model-compatible alignment, a separate presentation-attack detector, cosine similarity, and a calibrated threshold behind independent gates. Builds on CR-128 (face lab), CR-129 (face-match route) and CR-102 (engine gate). Status: slice 1 built 19 Nov 2026. Slices 2 to 4 are design only.

## Where the platform stands against that stack

| Part | Recommended | State |
|---|---|---|
| Detection | SCRFD or YuNet | built: YuNet |
| Alignment | model-compatible landmarks | built: YuNet's five landmarks into SFace's own alignment |
| Similarity | cosine | built |
| Comparison | benchmark EdgeFace, AdaFace, MagFace | **not built**: SFace only, never compared with another |
| Liveness | separate presentation-attack detection | **not built** |
| Decision | calibrated threshold, independent gates | **slice 1**: the measurement is built; no threshold has yet been calibrated or recorded |

## Slice 1 (built): choose the threshold from data

- `domain/face_calibration.py`: from scored pairs (same person, different people) it gives, for each false-match target (default 1 in 10, 1 in 100, 1 in 1,000), the threshold, the false match and false non-match rates, a 95% upper bound on each, whether enough pairs stand behind the target (at least `ceil(3 / target)` different-person pairs), and the equal error rate for comparing engines. A set that cannot be measured is refused with a reason. Pure and tested (`tests/unit/test_face_calibration.py`).
- `POST /v1/regulatory/face-lab/calibrate` (capability `regulatory.face_lab.run`): scores in, report out. The request is `{score, same}` pairs only, nothing that names or identifies a person; the audit record holds the engine and the set size, never a score.
- `tools/scripts/face_calibrate.py PHOTO_FOLDER`: runs a folder of photographs (one sub-folder per person) through the face service, keeps only the cosine of each pair and prints the report. No photograph leaves the machine or is written.

Rules:

- A calibrated threshold is a **necessary** record for the gate, never sufficient. It is measured on the pairs supplied. Document photographs against live selfies, other populations and presentation attacks are not covered, and a public set such as LFW is the easy case.
- The route's `face_threshold` stays the owner's choice in the market policy; nothing reads a calibration automatically. Recording one means the owner writes the engine, the set, its size, the false-match target and the threshold into the owner pack (`docs/signoff/face-engine-owner-pack.md`) and sets the policy value from it.

## Slice 2 (design): a separate presentation-attack detector

- A second sidecar (its own container, its own pinned model files), never merged with the comparison engine, so each can be evaluated and replaced alone.
- It answers a score per photograph and a stated attack coverage (print, screen replay, mask). The `BiometricEngine` port already has `liveness`; the sidecar adapter raises today, so a face match alone can never pass. The detector's adapter would replace that raise.
- Before any decision uses it: lab only (`LAB_ONLY` in the engine register), measured on an attack set the owner gathers (printed photographs, screen replays), with its own accept and reject rates recorded. A document photograph supplied by the person has no capture-time liveness at all, so this protects the **live** photograph only.
- Open-source detectors exist, and their code and weight licences differ: check each before adoption; record the result in the owner pack.

## Slice 3 (design): benchmarking other recognisers

- Candidates: EdgeFace, AdaFace, MagFace, against the current SFace. Each needs its weights obtained, pinned by SHA-256, its licence checked (research-only weights are common), and an adapter behind the same sidecar interface with the engine's own alignment.
- Compare by equal error rate and by false non-match at the owner's false-match target, on the **same** pair set and using `face_calibrate.py`, so the numbers are comparable.
- The winner, if any, is the one that turns fewer right people away at the target false-match rate on document-photograph-against-selfie pairs, not on a public set.

## Slice 4 (design): the independent gates

The decision keeps independent gates beyond a score: owner acknowledgement (built, `ZID_IDV_FACE_ROUTE_ACK`), engine gate with its records (built), a calibration record for the threshold in force (to add to the gate's list), an attack-evaluation record for the liveness detector (to add), a document authenticity check (not built: nothing yet shows the uploaded document is genuine), and the existing human review after the attempts are used.

## Not built

Slices 2 to 4; any automatic use of a calibration; a document authenticity check; a measurement on document-photograph-against-selfie pairs, which needs photographs of people who agreed.

Tests: `tests/unit/test_face_calibration.py`, `tests/unit/test_face_lab.py`.

## Update 19 Nov 2026: owner decisions and licence findings

**Owner decision: no human review.** The face-match route decides on the score alone, as CR-129 built it. An administrator-review step after the attempts were used was built for a day and removed again before deployment. A person who is not matched in three attempts is told the check has ended and to try again later or contact support.

**Attempts are one allowance per person.** Three attempts used to apply to each case, and "start again" opens a new case, so anyone could keep trying until a score passed. That is now closed: the capture attempts across a person's face-match cases in the last 24 hours count against one allowance (`FACE_ATTEMPTS_USED_UP`, HTTP 429; `application/idv_case.open_case`; `test_a_new_case_does_not_reset_a_persons_attempts`). With approval on the score alone and no reviewer, this limit is what stops a stranger sampling faces until one passes. It is per signed-in person, so it does not stop someone making new accounts: that needs the account-creation limits.

**Licences, checked from public sources on 19 Nov 2026 (not legal advice; the upstream LICENSE files were not read and each must be confirmed before adoption):**

| Candidate | Finding | Consequence |
|---|---|---|
| EdgeFace (Idiap) | Model cards (a mirror of the official one) give CC BY-NC-SA 4.0, a non-commercial licence; the official card points to a LICENSE file in Idiap's GitLab | not usable in a commercial product without a licence from Idiap |
| AdaFace | no licence for the pretrained weights found; the released models are trained on MS1MV2, MS1MV3, WebFace4M or WebFace12M | the datasets' own terms may restrict commercial use whatever the code licence says: unresolved |
| MagFace | an Apache-2.0 licence is shown on a mirror only; the released MS1MV2 weights state no separate terms | unresolved, same dataset concern |
| MiniFASNet (liveness, from Minivision's Silent-Face-Anti-Spoofing) | Apache-2.0, stated by two downstream projects, not read from the upstream repository | the one candidate that looks usable; still unmeasured |

Sources: [EdgeFace on Hugging Face](https://huggingface.co/idiap/EdgeFace-Base), [Idiap EdgeFace page](https://technology.idiap.ch/technologies/biometrics/fr-edgeface/), [AdaFace repository](https://github.com/mk-minchul/AdaFace), [MagFace repository](https://www.github.com/IrvingMeng/MagFace), [UniFace weights listing](https://huggingface.co/yakhyo/uniface-weights).

The practical effect: the benchmark in slice 3 cannot start from the weights that are easy to download. A face model whose weights are cleanly licensed for commercial use has to be found, or a licence obtained, before measuring it is worth the effort.
