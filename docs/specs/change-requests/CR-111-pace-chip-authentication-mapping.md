# CR-111: PACE with the chip authentication mapping (PACE-CAM)

Source: Wireframe 01 (ZID-DTV-WF-01) WF01-003, WF01-008; ICAO Doc 9303 part 11, section 4.4.3.5 and appendix I. Status: engineering built 18 Nov 2026. Nothing here has touched a real passport.

## What was built

- `evidence/application/pace.py`: the three object identifiers `id-PACE-ECDH-CAM-AES-CBC-CMAC-128/192/256` are usable (the mapping phase is the generic mapping). The terminal records the chip's mapping public key and the encrypted chip authentication data (tag 8A of the last answer). A chip that names the mapping and sends no proof is refused (`PACE_CAM_DATA_MISSING`); a proof from a chip that did not name the mapping is refused. CAM with 3DES stays unsupported, as the standard has it.
- `chip_authentication_data` and `verify_chip_authentication`: `CA_IC = D(KSEnc, A_IC)` with the IV `E(KSEnc, FF..FF)`, padding method 2 removed, exactly the size of the group order; the proof holds when `CA_IC x PK_IC` equals the chip's mapping public key.
- `evidence/application/chip_authentication.py`: reads the chip's static public key from data group 14 (standardized domain parameters only) and judges the proof. The key counts only if the signed object covered data group 14, otherwise the answer is NOT_ASSESSED, never a pass. A proof that fits no key of the run's curve is CLONE_SUSPECTED, a reason for a person to look and nothing more.
- `chip_reader.ChipMachine.chip_authentication()` carries the evidence out of a finished read; the relay hands it to the chip-link finish route, where it is judged against data group 14 after passive authentication and counted as a clone check beside Active Authentication. Either check finding a clone makes the session suspect.

## How it is proved

`test_chip_cam`: every value the standard prints for its worked example (session keys, both tokens, the encrypted chip authentication data, the chip's mapping public key and static public key), each way a changed proof, key or session fails, the verdicts against data group 14, and a full read of a simulated chip that holds its key and of one that only holds a copy of the public key. The existing PACE, chip reader and chip link tests still pass; three that used CAM as their example of an unsupported variant now use the integrated mapping with 3DES.

## Not built or not proved

- No real passport or phone. The chip simulator is written from the same standard as the reader.
- A chip whose data group 14 carries explicit domain parameters is not assessed.
- EF.CardSecurity is not read: the key comes from data group 14, which the signed object already covers.
