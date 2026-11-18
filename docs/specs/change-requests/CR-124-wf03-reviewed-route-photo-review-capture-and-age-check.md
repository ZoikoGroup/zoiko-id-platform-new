# CR-124: the reviewed (browser-only) identity route: the photograph is kept and reviewable, reviewers can decide, capture is guided, and the age check has a screen

Source: Wireframe 03 (ZID-DTV-WF-03) alternate route; WF01 section on the web-only path. Status: engineering built 18 Nov 2026. Never run with a real photograph, a real reviewer or a browser.

## A defect found first

The browser-only route told the applicant that two independent reviewers would look at their photograph. As built, they could not: the photograph was measured and digested and then dropped, and a reviewer's only act was to add a signature to a case whose responses carry codes. A second defect sat behind it: on a route with no chip, nothing ever established whether the document had expired (only the chip read set it), so under the default policy every such case stayed "indeterminate: document date not established" and could never be approved. Both are fixed here.

## What was built

- **The photograph is kept, encrypted** (`application/idv_media.py`, migration `verification_0024`): in the evidence vault under the applicant's own erasable key, held until the market's `media_retention_days` (1 to 90) ends. The table holds only the vault reference, digest, the measured image checks and the dates, never the image. **A market with no retention period keeps nothing, and the route refuses the photograph** (409 `MEDIA_RETENTION_NOT_SET`) instead of keeping it for ever. A new photograph or a recapture retires the old one.
- **Reviewers see it under control**: `POST .../media:access` (the existing protected act `evidence.object.read`, R3 and E3, a reason of 10 to 500 characters, never the applicant, only while the case is open) returns a grant that lasts five minutes and reads at most three times, bound to that reviewer. `GET .../media/{grant}` returns the image with `Cache-Control: no-store`. Every grant is a stored record with its reason. The image is in no list, log or other response.
- **Reviewers can decide**: `POST .../recapture` (a defined reason; earlier signatures are cleared, the old photograph can no longer be opened, the applicant is not charged an attempt) and `POST .../reject` (one of four defined failures; the case closes REJECTED with `REVIEWER_REJECTED:<code>`). Both are a reviewer's act under `verification.idv.review`, never the applicant's, and only on a route that people decide. `GET .../review` shows the case, whether a photograph exists, when it was taken, until when it is kept and what was measured.
- **The document's date comes from the platform's own reading** on such a route, only when every check digit on the printed lines held; otherwise it stays unknown, never "not expired". An expired document is decided by the market's policy (refuse by default).
- **The reviewer screen** (`admin/idv-cases`, `reviewer-panel.tsx`): the measured checks in words (with the plain note that a good score is about the capture, not authenticity), open the photograph with a reason, the time and read limits, ask for a new one, reject with a defined reason.
- **Guided capture** (`account/idv/document-capture.tsx`, `capture-logic.ts`): a camera frame in the shape of a passport data page, live hints (dark, glare, not sharp) one at a time, a photograph cropped to the frame, with the file picker kept as a fallback. The hints are unmeasured starting points; the platform judges the photograph it receives.
- **Applicant wording** matches: a recapture request costs no attempt; a rejection is not an accusation; an unreadable date asks for a retake.
- **The age-check screen** (`admin/age-check`): it says plainly that a typed birth date is self-declared and can never show "at least"; it can show "under" or "not decided". A verified age needs a chip read in the phone app or an approved estimate.
- **The photo-checking service**: its address now passes through compose (`ZID_OCR_URL`), and the method certification board reports "records missing" while it is not configured.
- Tests: `test_idv_media_pg.py` (the photograph kept encrypted, a grant per reviewer and three reads, a recapture retires it, rejection and approval by two reviewers, expiry from a reliable reading and never from an unreliable one), the expiry rule and the method board unit tests, and screen logic tests.

## Not built, not proved

- **No real photograph, reviewer or browser** has been through it. The screens were not opened in a browser, and the camera capture was not run on a phone.
- **Printed-document authenticity** (template, security features, photo substitution) is still not built; reviewers judge by eye, and the measured checks are about capture quality. Quality thresholds are proposals.
- **The photograph's retention period is the owners' choice** and ships unset, so nothing is kept until a market sets it. The DPIA, lawful basis and notice for keeping an identity document image are not done and are not code.
- **Reviewers see the photograph, not the printed data the platform read:** that is kept only as salted digests for the chip comparison, by design.
- **Selfie and face match on the browser route** remain parked (no engine approved).
- The photo-checking service is deployable but not running anywhere, and its Tesseract licence reading is not signed in the register.
