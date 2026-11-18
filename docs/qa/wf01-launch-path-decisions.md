# WF01 launch path: what is closed by engineering, what is closed by a decision, what only a person can do

Prepared 18 Nov 2026 (CR-114) for the product owner. These are recommendations with the reason; none is approved until the named owner records it. Nothing here claims a result that has not happened.

| Blocker | Closed how | Status | Who must still act |
|---|---|---|---|
| Android bridge never compiled | Compiled with JDK 17, Gradle and platform 34 on the development machine; also a manual CI job (`mobile-bridges.yml`) | See the build result in the CR-114 record | A person runs it on a phone |
| iOS bridge needs a Mac | The same CI file builds it on a hosted macOS runner, unsigned. No Mac of our own is needed to learn whether it compiles | Job written; **not yet run** (CI is manual) | Someone presses Run workflow once; a person with an iPhone and the NFC entitlement tests it |
| Real passports and phones | **Decision:** launch "supported" means only the phone and document combinations in the signed device matrix (`wf01-device-and-lab-pack.md` section 3). Everything else shows the non-NFC route. Until a matrix row passes, it is not claimed | Matrix written; no row run | QA runs it with a few consenting staff and their own passports |
| Lab report (PAD, face accuracy, fairness) | **Decision:** launch chip-only. The chip photo is not compared with a selfie, the face engine stays off, and the result is `VERIFIED_LIMITED`. The lab is then off the launch path (readiness plan item 4) | Already how the platform ships: engines are off without an approval record | Fund the lab only when a face engine is wanted |
| Provider contracts | **Decision:** launch on open registers and lists only. Licensed suppliers stay `DISABLED_UNTIL_CONTRACT`; a source that is not enabled is never called or billed (CR-112) | Built and tested | Procurement signs a contract per supplier when wanted |
| ICAO PKD membership | **Decision:** do not wait for the PKD. Use each launch country's own published master list through the scheduled fetch (CR-114), anchored on a certificate the PKI owner confirmed out of band | Fetch built, ships with no source | PKI owner chooses launch countries, confirms each anchor, and a second person approves |
| G0 sign-offs | The gate system records them against named roles and refuses a module that is not signed (CR-113). It cannot sign for anyone | Built and tested | The ten named roles sign with evidence |

## Recommended launch scope

One market, chip-only passports and chip ID cards, BAC and PACE, signed-object checks, Active Authentication or chip authentication where the chip offers it, issuer trust from approved country lists, no selfie, no licensed supplier. Everything outside that scope stays off by default.

## Exit conditions for that scope

1. The device matrix passes on at least one Android phone and one iPhone for the launch country's documents, recorded in the UAT register.
2. The launch country's list is staged, validated, approved by a second person and active.
3. G0 gates for the module are signed by their named roles, with no open defect.
4. DPIA reference and retention schedule are signed.
