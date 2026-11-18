# CR-114: scheduled master-list fetch, and the device and lab pack

Source: Wireframe 01 (ZID-DTV-WF-01), production-readiness plan items 2, 3, 5, 9. Status: engineering built 18 Nov 2026. Nothing here has run against a real country source, a phone or a lab.

## What was built

- **Scheduled master-list fetch** (`evidence/application/master_list_refresh.py`, worker step `master_lists`). For each source in `backend/config/pki/master-list-sources.yaml` it fetches the published CSCA master list and **stages** it in the governed trust store with its chain of custody. It never validates, approves, activates or rolls back: a person validates, a second person approves and activates (CR-107). The address must pass the same public-address check as every other outbound fetch (http or https, no private, loopback or link-local address, size cap). A list already held (same SHA-256) is not staged again. The shipped file lists **no source**, so nothing is fetched until the security owner adds one.
- **The device and lab pack** (`docs/qa/wf01-device-and-lab-pack.md`): build runbooks for the Android and iOS bridges, the device and passport matrix with the verdict each case must produce, the evidence a signer needs, and the lab test plan.

## Not built or not proved

- No source is configured, so the fetch has never run against a real publisher. Fetching is not trusting: the anchors still come from the security owner's out-of-band check.
- The Android and iOS bridges are still uncompiled and untested on a device. The pack tells a developer how; it does not replace them.
- The ICAO PKD needs membership and is not wired. Only a publicly reachable master list can be a source.
- The lab report, real passports and phones, and signatures are people's work.
