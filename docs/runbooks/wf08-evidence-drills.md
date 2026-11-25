# Evidence drills (ZID-CP-WF08 v2.0, E13; CR-170)

Written 25 Nov 2026 for the people who run the evidence service. It makes no certification or legal claim.

A drill is a rehearsal whose result is kept. The four drills below exist as tests, so they can be repeated exactly. What they prove is that the mechanics work against a real sealed receipt. **They do not prove that the production key store, the backups or the on-call rota behave the same way.** The first run in each production environment is still to be done, signed off by an operator, and filed.

## How to run, and what to keep

From the repository root, with the backend test environment and a test PostgreSQL database:

```
python -m pytest backend/app/tests/component/test_wf08_drills_pg.py -v
```

Keep, for each run: the date, who ran it, the commit, the full output, and any step that behaved differently from the expectation below. File it where the owners keep operational evidence. A drill with a surprise is a good drill: record the surprise and the fix.

| Run | When |
|---|---|
| Whole set | Before each release that touches evidence, signing or retention; and at least once a quarter |
| Drill A | Before and after any planned signing-key rotation |
| Drill B | After any real or suspected key exposure, once the new key is in place |
| Drill C | Before relying on the status list for a new customer; after any change to its publication schedule |
| Drill D | After any change to the retention periods |

## Drill A: restore after two key rotations

**Setup.** A receipt is sealed under the first signing key. The key is rotated twice. The published key set is written out and read back as a restore would.

**Expect.**
- The old bundle still verifies: integrity VALID, signer TRUSTED, proof COMPLETE, using only the published key set.
- Given only the newest key, the same bundle reads UNTRUSTED. This is why a restore must bring every historic public key, and why nobody may delete one.
- No private key appears in the published set.

**If it fails.** Do not rotate again. Restore the full key set from the last known good copy before anything else is signed.

## Drill B: rescoring after a compromised key

**Setup.** A checkpoint was signed at time T by key K. K is declared compromised from a time before T, then from a time after T.

**Expect.**
- Compromised from after T: the checkpoint stands (trusted, retired).
- Compromised from before T, or exactly T: it does not (compromised at signing). There is no benefit of the doubt at the boundary.
- Nothing is rewritten. The signed trust bundle lists K as compromised with the time, and a verifier that has fetched the new bundle rescored the history.

**After a real exposure.** Also publish a new trust bundle (it raises the version), tell every party that pinned keys, and list the affected receipts from the trust bundle's compromise time.

## Drill C: a status outage

**Setup.** The last status list fetched is five hours old and the endpoint is unreachable.

**Expect.**
- Fresh when fetched: ACTIVE.
- Five hours on, past its own expiry and older than the relying party accepts: UNKNOWN.
- No tolerance stated by the relying party: UNKNOWN.
- Nothing fetched at all: UNKNOWN.
- The verification result shows status now as UNKNOWN with the reason that it is not fresh enough. It never reads an old list as current.

**Operators.** During a real outage, say so to affected customers. UNKNOWN is the right answer; do not turn it into ACTIVE to keep traffic flowing. Revocations already published stay effective for anyone holding that list.

## Drill D: retention removes the body, the proof stays

**Setup.** A sealed receipt's bundle is reduced to what remains after the retention sweep: the ledger entry and its proof, without the body.

**Expect.**
- It still verifies as intact (structural only), signed by a trusted key, and says correctness is not assessed.
- Changing anything that remains (for example the commit time) is still caught.

This drill reduces the bundle the way the sweep does; the sweep itself, the erasure and the restore of tombstones are covered by `test_evidence_pg.py` and `test_restore_reapplies_tombstones`.

## Not yet done

- A production run of any drill.
- A cryptographic test campaign by someone who did not write the code.
- A drill of the timestamp authority, because none is contracted.
- A restore drill from real backups, which needs the operators' backup system.
