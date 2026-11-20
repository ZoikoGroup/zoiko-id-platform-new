# CR-128: an open-source face-match engine and an Operator Console Face lab

Source: CR-095 and CR-102 (self-run identity verification, the engine gate), CR-107 (biometric method lifecycle), `docs/signoff/face-engine-owner-pack.md`. Status: engineering built 19 Nov 2026. Not run with real photographs by engineering; the point of this change is to let people do that.

## Gap

The flow people ask for is: upload a passport or identity card, take a live photograph, compare the faces, and approve automatically if they match. The platform refuses to run any face engine until an independent evaluation, a presentation-attack lab test, a data protection impact assessment and licence records exist, and until this change there was no engine that could even be tried: only adapters for libraries nobody had installed. People could not measure anything.

## What was built

- **A face-match sidecar** (`tools/face-sidecar`, image `zid/face`): OpenCV's YuNet face detector and SFace face recogniser behind a small HTTP service, in its own locked-down container (read-only, no privileges, 768 MB) so the backend links no imaging or model library. The two model files are downloaded at build time and refused unless they match pinned SHA-256 digests. It returns a similarity, never a decision; it refuses an image with no face or more than one face; it stores and logs nothing. `POST /liveness` answers 503 `LIVENESS_ENGINE_NOT_INSTALLED`: there is no open-source liveness model in the image.
- **An adapter** (`verification/infrastructure/face_lab.py`, `SidecarFaceEngine`): the sidecar behind the `BiometricEngine` port. It raises, and so is treated as PENDING, whenever the sidecar cannot decide; its `liveness` always raises. It is still refused by `engine_gate` until every record in `self-run.yaml` exists.
- **A Face lab** (Operator Console, Regulatory): `GET /v1/regulatory/face-lab` (is the engine reachable, where the approval gate stands and every record still missing) and `POST /v1/regulatory/face-lab/compare` (two photographs in, a measurement out), under the new operator capability `regulatory.face_lab.run` (one person, reads no customer data). The audit record names the engine and which side of the suggested threshold the result fell, never a photograph and never the score.
- **Register entries** for both models in `engine-register.yaml`, as LAB_ONLY with licences unverified: SFace was trained on a public face dataset whose commercial-use terms are not confirmed here.
- **A config fix**: the two engine loaders now follow `ZID_PLATFORM_CONFIG` like every other loader, so the readiness answer works in the deployed image.

## Rules

- A lab measurement is not an approval. Nothing here lets anyone through: the gate's own refusal stands until the evaluation, lab test, impact assessment and licence records are made by the people who own them.
- A face match alone can be shown another person's photograph. Without a liveness result the platform will not approve anyone on a match, and the lab image has no liveness model.
- Use photographs only of people who agreed to the test. The lab stores nothing, but a measurement of a real person's face is biometric processing.
- The suggested cosine threshold (0.363) is the model authors' starting value, not a decision: the real threshold comes from an independent evaluation.

## Not built

- A liveness engine (Silent-Face-Anti-Spoofing needs PyTorch, which is far heavier than the memory this setup has; and any liveness model needs its own independent attack test).
- The live-capture step in the applicant's identity page and the decision rule that would approve on face match plus liveness. Both stay behind the gate; building them before an engine can be approved would be a screen that can never run.
- The independent evaluation itself.

Tests: `tests/unit/test_face_lab.py`, `tools/face-sidecar/smoke_in_container.py`.
