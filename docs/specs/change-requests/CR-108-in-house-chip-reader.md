# CR-108: Our own passport and ID chip reader: Basic Access Control, secure messaging and file reading (ZID-DTV-WF-01 WF01-003, WF01-008; builds on CR-095, CR-102, CR-107)

Status: **built on the engineering owner's instruction of 2026-11-17 ("close these without depending on a third-party app"); sign-offs UNSIGNED. Proved against the ICAO standard's worked example and a simulated chip. Never run on a physical passport.**

## Why

The self-run path (CR-095, CR-102) judges what a chip signed, but nothing in this project could get the signed object and the data groups off a chip: the contract assumed somebody else's phone app would. A browser cannot do it (Web NFC reads NDEF tags only). This change writes the protocol ourselves, once, in plain Python, so that any thing that can carry a card command to the chip and bring the answer back can read a passport for us: a phone's NFC bridge, a relay, or a desktop smart-card reader.

## What was built

`evidence/application/chip_reader.py`, exported through `evidence.ports` (`ChipReader`, `ChipRead`, `ChipReadError`). It is sans-I/O: it is given one async function that sends a command APDU and returns the response.

- **Basic Access Control** from the document number, date of birth and expiry date: the key seed, the two keys, the mutual authentication (our random numbers, the chip's answer checked for its MAC and for both random numbers) and the session.
- **Secure messaging**: 3DES with ISO/IEC 9797-1 MAC algorithm 3 and the send sequence counter. Every command and every response moves the counter, so a captured response cannot be replayed.
- **Reading**: select the LDS application, then EF.SOD (mandatory) and the data groups DG1, DG2 (optional), DG14, DG15; a group the chip does not have is skipped and named. The result is exactly what `POST /v2/documents/chip-verify` and the self-run chip route take.
- **The status of a protected response is read from inside it** after its MAC checked, whatever the outer status says.
- **It judges nothing.** Whether the data is genuine is `epassport.verify`'s job against a country list a person approved in the trust store (CR-107).

## How it is proved

Every value Doc 9303 part 11 appendix D prints for its BAC example is asserted: the key seed (`239AB9CB…`), both access keys, the cryptogram and MAC the reader sends (`72C29C23…`, `5F1448EE…`), the chip's answer, the session encryption key and the send sequence counter. The session MAC key is checked against the same derivation, because the printed value could not be confirmed from the source available while writing. A simulated chip (the chip side of the same protocol, in the tests) is then read end to end and what comes back verifies as `VERIFIED_LIMITED` under its country CA. Further tests: wrong document details give no session and name PACE as a likely reason; a PACE-only chip is reported, never guessed at; a wrong MAC stops the read; a replayed response is refused; a missing signed object is an error and a missing group is not; the document details are checked before anything is sent.

## The relay (added the same day), and why it keeps no state

A phone or a desktop reader does not run the protocol: it only carries card commands, and our server runs the reader. Each command is its own authenticated HTTP request, so the existing authentication covers every step and no WebSocket is needed.

- `POST /v2/self-run/verifications/{id}/chip-link` opens a read with the three printed document details (`document_number`, `date_of_birth`, `document_valid_to`; the last is the document's printed end date: the card-data guard bans a field called `expiry`) and returns the first command and a sealed `token`. `.../chip-link/step` takes `{token, response_base64}` (the chip's answer and the token that came with the command) and returns the next command and a fresh token, or the end of the read (state `READ`: which groups were read, whether Active Authentication was answered). `.../chip-link/finish` takes `{token, face?}` and judges what was read exactly as the chip route does (`_judge_chip`: passive authentication against the configured or governed country list, the profile decides).
- **No state on the server.** The protocol is a `ChipMachine` (an explicit state machine, tested equal to the async reader). Between steps its whole state is sealed (AES-256-GCM under a data key wrapped by the tenant's key through the evidence runtime, the same envelope the stored uploads use) and handed to the device. So any server process takes any step: no sticky routing, no registry to lose, nothing to clean up. A token opens only for the company, person, client and session it was issued to (anyone else's looks exactly like no token), cannot be read or changed, and expires on the server's clock.
- **Active Authentication runs through the relay**: the server sends `INTERNAL AUTHENTICATE` with the session's own challenge when DG15 is present, so the clone check no longer depends on the app.
- **Limits, each tested:** 120 s for the whole read, 30 s per answer, 600 commands, 4 KB per answer, 360 KB per token, strict input patterns. There is no per-company cap on open reads because the server holds nothing per read; each step is an ordinary authenticated request.
- **What a replay can and cannot do:** an old token with an old answer returns the command it returned before. Once secure messaging is active the chip's counter has moved on, so the chip refuses it and the read fails; in the early plain steps a replay only restarts the same read (a chip lets Basic Access Control run again). A read is judged once because the SESSION is decided once, not because a token is used up.
- **What the token holds:** the document details' keys and the session keys and everything read so far. It is a secret: it only ever leaves the process sealed, and a deployed key service (or a shared keystore) is needed so every process can open it. With the development key provider, whose keys live in one process's memory, a token does not survive a restart; that is a development limitation, not a product one.
- **The device side:** `readChipThroughRelay` and `finishChipRead` in `sdks/typescript/src/chip-relay.ts` (tested with a scripted platform). If the device fails it simply stops: nothing is left running and the token expires. The native `transceive` it needs is reference source in `sdks/native-bridge/README.md`: **not compiled, not run**.

## PACE (added the same day, once the standard was available)

The ICAO Doc 9303 part 11 PDF (eighth edition, consolidated with Amendment No. 2 of 23 February 2026) was supplied, so PACE was built and proved against it instead of being guessed at.

- `evidence/application/pace.py`: the terminal's side of PACE with the ECDH generic mapping. `ChipMachine` first looks for EF.CardAccess; a chip that has one supports PACE, which the standard prefers, so the machine runs it when the chip offers a variant implemented here, then selects the application under AES secure messaging (`AesChannel`). With no usable PACE it reads with Basic Access Control as before. `try_pace=False` skips the probe.
- **Proved against the standard**: every intermediate value of Appendix G.1 (BrainpoolP256r1, AES-128): the password key, the decrypted nonce, the Diffie-Hellman point H, the mapped generator, the shared secret, both session keys, both token inputs and both tokens. The whole exchange is also replayed through the real machine with the chip's printed responses, asserting each command it sends equals the standard's printed APDU byte for byte.
- **Proved against the crypto library**: the curve constants (derived from the library's own curves; BrainpoolP256r1 also equals the listing printed in the standard), the elliptic-curve arithmetic on every supported curve, and complete exchanges on every curve at AES-128, -192 and -256.
- **A correction the standard let me make**: the Basic Access Control secure-messaging commands and responses (Appendix D.4) were not asserted before; they are now, byte for byte, and the printed `KSMAC` replaced a derivation workaround in an earlier test.
- **Not proved against a printed vector**: the AES secure channel after PACE. The standard prints no worked example for it (only the PACE tokens, which prove the CMAC). It follows the written rules (IV = E(KSEnc, SSC), a 128-bit counter starting at zero, an eight-byte CMAC over the counter and the padded data) and is checked against a chip simulator written separately from it. A real chip is the only thing that closes this.
- The elliptic-curve arithmetic is written here (the library exposes only the x-coordinate of a key agreement and the mapping needs a full point) and is **not constant time**: the private values are single-use ephemeral keys protecting one read.
- **Failure behavior**: a wrong password fails at the authentication step (status 6300 reported as wrong document details, nothing is read); a chip that cannot show it holds the same keys is refused (`PACE_TOKEN_INVALID`); a bad or equal public key is refused; a malformed EF.CardAccess fails closed.

### The integrated mapping (built after the generic mapping, from Appendices B and H of the same standard)

- `pace.rp` is the pseudo-random function of section 4.4.3.3.2 (AES in CBC mode keyed by the terminal's nonce t, then chained with the two constants, enough blocks for log2 p plus 64 bits) and `pace.encode_to_group` is the point encoding fG of Appendix B.2. The terminal sends t in clear in the mapping step, the chip answers with an empty data object, and both sides take the mapped generator fG(Rp(s, t)); the key agreement, tokens and AES channel are the same as for the generic mapping.
- **Proved against the standard**: Appendix H.1 (BrainpoolP256r1, AES-128), value by value: the decrypted nonce, R(s,t) (three blocks), Rp(s,t), the x and y of the mapped generator, the terminal's public key, the session keys, both token inputs and both tokens (`TIC` 75D4D96E 8D5B0308, `TIFD` 450F02B8 6F6A0909). Also checked on every supported curve and key size that the encoded point lies on the curve and in the prime-order subgroup.
- **Not proved against a printed vector**: AES-192 and AES-256 (the 256-bit constants are copied from the standard's text; the standard prints a worked example only for AES-128), and the integrated mapping on a real chip.
- **Failure behavior**: NIST P-224 is not used with it (the chip is read another way, or the refusal names the reason); a chip that answers the nonce with data is refused; a nonce of the wrong length is refused.

### DH groups, 3DES session keys, the CAN and the three other Brainpool curves (added last)

- **DH groups 0, 1, 2** (RFC 5114 MODP 1024/160, 2048/224, 2048/256): the mapping is g' = g^s * h (generic) or Rp(s,t)^((p-1)/q) (integrated), public values are unsigned integers as long as the prime, and every peer value is validated to have order q (RFC 2631) so it cannot confine the secret to a small subgroup. The token's public key object carries tag 84 for a DH value (86 for a curve point). 2048-bit values do not fit a short APDU, so the machine uses extended-length encoding for those commands.
- **Proved against the standard**: Appendix G.2 (DH generic mapping, group 0) and H.2 (DH integrated mapping, group 0), value by value through the session keys and both tokens (`TIFD` B46DD9BD 4D98381F and `TIC` 917F37B5 C0E6D8D1; `TIC` C2F04230 187E1525 and `TIFD` 55D61977 CBF5307E). The two 2048-bit groups are checked by arithmetic (primality, subgroup order, generator order, membership checks) and by complete exchanges through the machine.
- **3DES session keys** (generic mapping): two DES keys from SHA-1, parity adjusted; the nonce is decrypted with 3DES in CBC mode; the tokens are the retail MAC; the session then uses the same 3DES secure messaging as after Basic Access Control, with a zero counter. **Not proved against a printed vector** (the standard prints none): it follows the written rules and is exercised against the separately written chip simulator. The integrated mapping with 3DES is not offered because the standard does not say how Rp is built for DES.
- **The CAN**: the password is the Card Access Number itself (not hashed), password reference 2 in MSE:Set AT. A read can be opened with only the CAN (`can` in the chip-link start request) or with the three document details; given all three, the details are used. The CAN is a PACE password only, so a chip with no usable PACE is refused with `CAN_NEEDS_PACE` and **no Basic Access Control command is sent** with keys the machine does not have. The CAN is never echoed in an error.
- **BrainpoolP192r1, P224r1, P320r1**: the constants come from RFC 5639 and are proved by arithmetic in the tests: the field is prime and p is 3 mod 4, the generator lies on the curve, n is prime, n times the generator is the identity, (n-1) times it is its negative, the order is within the Hasse bound, the group laws agree, and complete exchanges on each curve (generic mapping with AES and 3DES) succeed. They are not checked against the crypto library, which lacks them.
- **Still open**: all of this against a real chip. Nothing here has touched a passport.

## What it does not do

- **Some PACE variants.** Implemented: ECDH and DH (the three MODP groups of RFC 5114), each with the generic mapping and the integrated mapping with AES-128, -192 or -256, and the generic mapping with 3DES session keys; the password is the MRZ or the Card Access Number. Curves: NIST P-192, P-224 (not with the integrated mapping, which the standard excludes), P-256, P-384, P-521 and Brainpool P192r1, P224r1, P256r1, P320r1, P384r1, P512r1. Not implemented, and refused by name: the chip-authentication mapping, the integrated mapping with 3DES (the standard does not say how its pseudo-random function is built), and explicit domain parameters. A chip that offers only those is read with Basic Access Control if it also accepts it; otherwise the refusal names the variant it offered.
- **It has not read a real passport.** The standard's vectors and a simulator are not a chip. A device test with real documents (and a test matrix of readers) is still needed before this is called working in the field.
- **No native code that has been built.** The phone side is 30 to 60 lines per platform that only transmit bytes; reference source is in `sdks/native-bridge/README.md`, written from the vendors' documented APIs, never compiled or run. It needs a developer, a device matrix and real passports.
- **No Chip Authentication.** Active Authentication is driven (through the relay) and checked; Chip Authentication (DG14) is not.
- **No selfie capture.** The relay's finish step accepts a face and passes it to the same judgement; no camera screen exists, and the media is ignored unless the deployment has an accepted engine (which needs an independent evaluation).
- BAC protects with printed data (3DES, a key from the document number and two dates). It is old by design and weak against someone holding the document; it is the standard's protection for a read the holder started.

## Owner decisions still needed

1. Who builds and tests the native bridge shells, and where the real passports and phones for the device matrix come from. A deployed key service (or a shared keystore) is also needed so that every server process can open the relay's sealed tokens.
2. Whether the PACE variants named above that are not implemented matter for the launch markets (the chip-authentication mapping in particular), and where real test passports and readers come from.
3. Whether chips that only offer PACE route to an assisted or provider path in the meantime (the alternatives route already lists the choices).

## Evidence

`backend/app/tests/unit/test_chip_reader.py` and `backend/app/tests/unit/chip_simulator.py` (the reader); `backend/app/tests/component/test_chip_relay_pg.py` (the relay over HTTP against PostgreSQL); `sdks/typescript/test/chip-relay.test.ts` (the device-side loop).

## Sign-off (UNSIGNED)

| Role | Name | Date | Decision |
|---|---|---|---|
| Security / PKI owner | | | |
| Mobile lead | | | |
| Identity Risk | | | |
