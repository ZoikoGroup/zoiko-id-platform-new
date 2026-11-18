# WF01 device and lab pack

Prepared 18 Nov 2026 by engineering (CR-114). It tells a developer, a tester and a lab exactly what to do and what counts as a pass. It approves nothing and records no result: results go in the UAT register by the person who ran them.

## 1. Build the Android bridge (`sdks/native-bridge/android`)

1. Install JDK 17 and Android Studio (or the command-line tools with platform 34 and build-tools 34).
2. Open `sdks/native-bridge/android` as a Gradle project. Align the plugin versions in `chipbridge/build.gradle.kts` with the host app's.
3. `./gradlew :chipbridge:assembleDebug`. Fix what the compiler reports; the Kotlin was written without one.
4. Add the module to the host app; call `NfcChipReader(activity, webView).start(...)`; the page calls `transceiveFromNative(window.ZoikoChip)` then `readChipThroughRelay(...)` (`sdks/typescript`).
5. Check on a phone with NFC: the app installs on a phone **without** NFC (the manifest marks it optional), and the other route is offered.

Pass: the module builds, and section 3's cases give the listed verdicts.

## 2. Build the iOS bridge (`sdks/native-bridge/ios`) on a Mac

1. Xcode 15 or newer; an Apple Developer account with the **Near Field Communication Tag Reading** capability on the app ID.
2. Add the Swift package to the app target. Info.plist: `NFCReaderUsageDescription`, and `com.apple.developer.nfc.readersession.iso7816.select-identifiers` containing `A0000002471001`.
3. Build for an iPhone 7 or newer (iOS 15 or later). Fix what the compiler reports.
4. `NfcChipReader.attach(to:)` on the web view configuration before the page loads; `begin()` on a user tap.

Pass: builds, and section 3's cases give the listed verdicts.

## 3. Device and passport matrix

Run each case on each phone in the matrix (record model, OS version, app build). The tester types the document number, date of birth and expiry (or the CAN), with the holder's consent. The bridge never sees them.

| # | Document | Expected platform verdict |
|---|---|---|
| D1 | Current passport, BAC only | Read completes; data-group hashes match the signed object; issuer signature chains to an **active** country list; Active Authentication passes if the chip has DG15 |
| D2 | Current passport, PACE (generic mapping) | As D1 over PACE |
| D3 | Current passport, PACE with the chip authentication mapping (CR-111) | As D1; chip authentication proved when DG14 is covered by the signed object |
| D4 | Passport from a country **not** in the active list | Not verified, with the reason "issuer not trusted"; never "verified" |
| D5 | Expired passport | Read completes; expiry reported; not treated as current |
| D6 | National ID card with chip | Read as the card's profile; same chain rules |
| D7 | Wrong document number or date of birth | The chip refuses; the person is told to check what they typed; no retry storm (attempt cap) |
| D8 | Phone moved away mid-read | Read stops; nothing is left running on the platform; the token expires; "hold still and try again" |
| D9 | Photo page copy or a non-chip card | "Not a passport chip"; no verdict |
| D10 | A **known clone** or test-lab clone chip, if the lab supplies one | The clone check fails (Active Authentication or chip authentication); never verified |

Any case that gives a different verdict is a defect, not a note. Record the phone, document (type and country only, never its number), the verdict and the reason code.

## 4. What a signer needs

| Gate and role | Evidence to bring |
|---|---|
| G0 Legal, DPO | The DPIA reference and lawful basis per market; the retention schedule; the country lists Legal approved |
| Security PKI owner | Out-of-band confirmation of each anchor fingerprint (who, how, when); the list staged, validated and the second approver's name |
| QA | The completed section 3 matrix for each phone, with defects closed |
| Engineering | The build numbers of both bridges and the test report |
| Mobile lead | Store release owner named; both apps pass section 3 |

The platform records a signature only against the gate's named role (`/v1/screening/gates` for screening; the trust store for lists). It cannot sign for anyone.

## 5. Master-list fetch: how to switch it on

1. The security owner obtains the publishing country's CSCA or master-list-signer certificate **from that country's own publication** and compares its SHA-256.
2. Put the certificate under `backend/config/pki/anchors/` and add a source to `backend/config/pki/master-list-sources.yaml` (id, url, tenant, anchor file, the fingerprint they typed back).
3. The worker stages a candidate when the list changes. A reviewer validates it, a second person approves and activates it. Until then nothing trusts it.
4. To stop: remove the source. Activated lists stay governed by the trust store (retire or roll back there).

## 6. Lab test plan (PAD, face accuracy, fairness; ISO/IEC 30107-3)

Engineering supplies: the build under test, the engine register entry (components, versions, licences), the thresholds in force, and the decision rules. The lab supplies the method, the population and the report.

1. **Scope:** engine and version, the market, the capture channels (chip photo against a live capture).
2. **PAD:** attack types (print, screen replay, mask) per the lab's protocol; report APCER and BPCER at the platform's threshold.
3. **Accuracy:** FMR and FNMR at the platform's threshold on a population representative of the market.
4. **Fairness:** the same rates split by age band, sex and the region groups the lab can measure; any group outside the agreed bound is a finding.
5. **Output:** a signed report with method, population, thresholds and results. Engineering enters the report reference in the engine record; the platform keeps the engine off until the record, the DPIA reference and an approver exist.

Not in this pack and not claimed: that any engine passes. Only the lab can say that.
