# Self-run identity verification: what the phone app must do (contract for the mobile team)

Status: **contract only; no app exists.** The server side is built (CR-095, CR-102) and is OFF unless `ZID_SELF_RUN_ENABLED` is set. Nothing here is certified, and the biometric half is off until an engine has an independent evaluation. This document says exactly what the platform expects from a native app so it can be built and tested against it.

## Why a native app

Reading a passport or eID chip needs NFC with ISO/IEC 14443 and the ICAO 9303 protocols. A web page cannot do it: Web NFC does not expose ISO 14443 chips. The server only checks what the app sends; it never talks to a chip.

## The flow

1. **Start.** The company's backend (not the phone) calls `POST /v2/self-run/verifications` with `profile_id`, `subject_principal_id`, `purpose`, `jurisdiction` and a service token with scope `verification:execute`. The answer holds `verification_id` and `aa_challenge` (16 hex characters = 8 bytes). Pass both to the app. Send the header `x-zid-device` with a stable per-install id if the company wants device linking (it is hashed with a secret and never stored raw).
2. **Read the chip** (the app, with the holder's consent and the document's access key from the MRZ or CAN):
   - establish the channel (PACE, or BAC where the document only supports it);
   - read **EF.SOD**, **DG1** (MRZ) and **DG2** (face image); read **DG14** if present; read **DG15** if the chip supports Active Authentication;
   - if DG15 exists, send the chip an **INTERNAL AUTHENTICATE** command carrying the 8-byte `aa_challenge` (decoded from hex) and keep its response bytes unchanged;
   - if the chip offers **Chip Authentication** (DG14), run it for the app's own secure-messaging keys. The server does not see or check this: it protects the channel on the phone, and a failure there means the app must not trust what it read.
3. **Capture the face** (only if the company's deployment has an accepted engine, which the app learns from the company, never assumes): one sharp live image of the holder and a short liveness capture. Follow the accessible alternatives in `capture_alternatives`: a person who cannot use the camera must be offered another route, and choosing it is never a failed attempt.
4. **Send.** `POST /v2/self-run/verifications/{verification_id}/chip` (through the company's backend or a token the company issues to the app):

```json
{
  "sod_base64": "<EF.SOD bytes>",
  "data_groups": { "1": "<DG1>", "2": "<DG2>", "14": "<DG14>", "15": "<DG15>" },
  "active_authentication": { "response_base64": "<INTERNAL AUTHENTICATE response>", "hash_name": "SHA-256" },
  "face": { "probe_base64": "<live image>", "liveness_base64": "<liveness capture>" }
}
```

   `active_authentication` and `face` are optional. Include DG15 whenever you send `active_authentication`. For an ECDSA key the response is the plain `r || s` signature. For an RSA key it is the full ISO/IEC 9796-2 signature. `hash_name` is optional and only for ECDSA: leave it out unless DG14 names a hash.
5. **Read the answer.** `status` is `VERIFIED`, `FAILED`, `MANUAL_REVIEW` or `IN_PROGRESS`. `reason_codes` says why, in codes. `aa_challenge`, when present, is a NEW challenge: the one used is spent. If `status` is `IN_PROGRESS` the chip could not be judged yet (for example the issuing country is not configured): keep the session, read the chip again, and use the new challenge. Never reuse a challenge or a captured answer.

## Reason codes the app can show in words

| Code | Meaning for the person |
|---|---|
| `SELF_RUN_CHIP_INDETERMINATE`, `NO_CSCA_CONFIGURED` | We cannot check documents from this country yet. Nothing was decided. |
| `SELF_RUN_CHIP_NOT_VERIFIED`, `DATA_GROUP_HASH_MISMATCH` | The document data did not check out. |
| `SELF_RUN_CLONE_CHECKED` | The chip proved it holds its private key. |
| `SELF_RUN_CLONE_NOT_CHECKED` | The chip was not asked to prove it, so a copy would not be detected. |
| `SELF_RUN_CLONE_SUSPECTED` | The chip did not prove it. This goes to a person; it is never a pass. |
| `SELF_RUN_BIOMETRICS_OFF` | No face check is switched on, so nobody was matched to the document. |
| `SELF_RUN_FACE_MATCHED_TO_CHIP_PHOTO` | The live face matched the photo on the chip. |
| `SELF_RUN_FACE_NO_MATCH` | The live face did not match. |
| `SELF_RUN_LIVENESS_UNDER_THRESHOLD` | The liveness check was not convincing. This goes to a person. |

The app must never say "verified" itself, say "rejected" for `MANUAL_REVIEW` or `IN_PROGRESS`, or hide that a person may look at a case.

## Rules the app must keep

- **Consent first, and only for what is needed.** The privacy gate runs on the server at start; the app shows the notice the company gives it and records nothing without consent.
- **Keep nothing.** Chip data, the face image and the liveness capture are sent once and not stored on the phone after the answer, apart from what the operating system needs to finish the call.
- **No screenshots or logs of chip data or media.** Crash reports must not contain them.
- **No retry loop on the same challenge.** Each attempt uses the challenge the last answer returned.
- **Fail closed.** If the chip read, Chip Authentication or Active Authentication fails on the phone, send nothing that looks like success. Send what was read and let the server say what it is worth, or stop.
- **Never decide.** The app does not compute a score, a match or a verdict.

## What the server will refuse

`404 SELF_RUN_NOT_ENABLED` while the deployment has not switched the path on; `409 SESSION_NOT_AWAITING_EVIDENCE` for a session already decided; `422 VALIDATION_FAILED` for data that is not base64; `403 SCOPE_INSUFFICIENT` without the scope. A face or liveness capture sent when no engine is accepted is ignored, not stored and not an error.

## Test vectors the mobile team needs from us and from a real chip

- The server tests use made-up chips (a test country signing authority, ECDSA and RSA keys). The RSA Active Authentication check follows ISO/IEC 9796-2 scheme 1 as read from the standard and has **no real passport vector yet**: before it is relied on, run it against chips from at least two issuing countries and fix any difference.
- Real country signing certificates (`config/trust/csca/`) are an operations task; the path answers "indeterminate" until they are imported.

## Not covered here

PACE, BAC and Chip Authentication are between the app and the chip. The server cannot check them, which is why the app must treat a failure there as a failure. The no-chip path (a document with no chip) is not part of this contract: it needs a product and legal decision first.
