# CR-145: the rehearsal runs its own RFC 3161 timestamp authority

Source: owner instruction, 20 Nov 2026 ("anything that needs a VM, complete it by creating an image"). Builds on CR-060 (software keys, the external timestamp as the mitigation). Status: built 20 Nov 2026.

## The gap

The production-image rehearsal ran with `ZID_EVIDENCE_TSA_WAIVER=accept-no-external-timestamps`: no checkpoint was timestamped, so the timestamp client, the token verification and the trust file had never run against a real authority, and a rewritten evidence history could not be told from the original. The honest alternative to sending digests to a public service is an authority on this machine.

## What was built

- **`deploy/local-prod/tsa/`**, image `zid/tsa:prodlocal`: an RFC 3161 authority built on the `openssl ts` command the base image already has, with no downloads. On first start it makes a root and a signing certificate (`timeStamping`, critical) and **deletes the root's private key**, as an offline root is kept; `/ca.pem` is the root, `/health` its health. It runs as a non-root user, answers only `application/timestamp-query`, rejects a request over 4 KiB, and serialises answers on one serial file.
- **The stack**: the `tsa` service, a `tsa-data` volume mounted read-only into the cell and worker at `/tsa`, `ZID_EVIDENCE_TSA_URL=http://tsa:8318/` and `ZID_EVIDENCE_TSA_TRUST_FILE=/tsa/ca.pem`, the waiver removed, and the cell and worker waiting for the authority to be healthy. `rehearse.sh` builds the image.
- **`tools/scripts/tsa_check.py`**: asks any authority for a timestamp with the platform's own client and verifier and requires (1) acceptance against the root named, and (2) refusal by a verifier that trusts an unrelated root, so "accepted" means the token really chains to the root you named. Exit 0 only when both hold. It is for the authority the owners choose, before `ZID_EVIDENCE_TSA_URL` is set in production.

## Where this stops

- It is **not a trusted third party**. It trusts nobody but this rehearsal and is no substitute for an authority the owners chose and contracted; a real deployment sets the URL and the trust file to that authority's. It proves the production path (client, token verification, trust file, sealing) runs end to end.
- The authority's signing key sits in a Docker volume on the same machine, so a person with the host could re-sign. That is exactly the exposure a real, separate authority removes.
- The rehearsal authority's signing certificate lasts 825 days and cannot be renewed (the root key is gone): a reset of the `tsa-data` volume makes a new authority, and earlier tokens then verify only against the old root.

Verified: `tsa_check.py` passes inside the stack's network with the production image (accepted under the authority's root, refused under an unrelated one).
