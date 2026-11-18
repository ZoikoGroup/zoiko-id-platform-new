# Reviewed (browser-only) identity route: user acceptance script

Prepared 18 Nov 2026 by engineering for QA. **Nothing here has been run.** The screens were never opened in a browser and the camera never tried on a phone; this script is how that is done and recorded. It records no result: the person who runs it fills in the table at the end and attaches it to the UAT register.

## Before you start

- A market in `backend/config/idv/market-policies.yaml` with the `WEB_REVIEW` alternate (`reviewers_needed` at least 2), `media_retention_days`, `result_valid_days` and, to test the queue, `review_sla_hours`. Without a retention period the route refuses the photograph (that is test 2).
- The photo-checking service running: `docker compose --profile ocr up -d --build ocr`, then `ZID_OCR_URL=http://ocr:8090` on the backend. Run `docker exec -i zoiko-id-ocr-1 python - < tools/ocr-sidecar/smoke_in_container.py` first: it must end `ALL PASSED`.
- Three signed-in people in one company, each with the review actions: an **applicant** and two **reviewers**. Use test documents or your own, with consent. Never a real customer's.
- Phones: at least one Android and one iPhone with a current browser. Desktop: one Chromium and one other browser.

## The tests

| # | Do this | Expect |
|---|---|---|
| 1 | Applicant opens **Identity check**, starts with "Verify in this browser only" for the market | A case opens; the steps are Start, Notice, Photo, Status. No chip or selfie step |
| 2 | In a market with **no** retention period, send a photograph | Refused with "not available for your country yet"; nothing is kept |
| 3 | Tap **Use the camera** on a phone | The browser asks for the camera; a dashed frame in the shape of the data page; a hint line that changes with light and glare |
| 4 | Cover half the lens, then tilt the page into glare | The hint says "too dark", then "glare"; it says one thing at a time |
| 5 | Take the photograph | "Ready to send"; the picture is the frame's contents; the camera light goes off |
| 6 | Deny the camera permission, then try again | A plain message, and the file picker still works |
| 7 | Choose a photograph from the device instead | It is accepted; a PDF or a too-small file is refused before sending |
| 8 | Send a photograph with the two lines at the bottom unreadable | The case says it could not read the date and asks for a retake; it is never approved |
| 9 | Reviewer opens **Identity proofing cases** | The case shows under "Waiting for a reviewer" (not for the applicant) with how long it has waited |
| 10 | Reviewer tries **Open the photograph** with a one-word reason | Refused; with a real reason it opens |
| 11 | Look at the opened image | It is upright; right-click and inspect: no location or device metadata in the downloaded file |
| 12 | Wait six minutes, or open it four times | The permission is spent; ask again with a reason |
| 13 | The applicant tries to open their own photograph as a reviewer | Refused |
| 14 | Reviewer 1 asks for a new photograph ("glare") | The applicant is told it is not a failed attempt; the old photograph can no longer be opened; earlier signatures are cleared |
| 15 | Applicant sends a new photograph; both reviewers sign | Approved; the case says what claim it left and until when; a second signature by the same person is refused |
| 16 | On a document with no machine-readable lines, reviewer attests "in date" | The case records an attestation; a second attestation is refused |
| 17 | Reviewer rejects a different case for "document expired" | Closed rejected; the applicant is told, without an accusation; the photograph can no longer be opened |
| 18 | Applicant asks for their data (access request) and then erasure | The export lists the case and says a photograph is held and until when, never the image; after erasure the claim is withdrawn and the photograph retired |
| 19 | Send a file containing the EICAR test string | Refused; nothing kept |
| 20 | Stop the photo service and send a photograph | "Not available right now, try again"; nothing kept or read |
| 21 | Wait past `media_retention_days` (or shorten it in a test market) | The photograph is gone; the case and its decision remain |

## Accessibility (do these on the reviewer screen and the applicant page)

Keyboard only, through every step; a screen reader reading the camera hint, the steps and every error; text at 200 percent; the dashed frame and the hints not relying on colour alone. Record what failed and where.

## Record

| Test | Device and browser | Pass or fail | Notes, with the case id (never a document number) |
|---|---|---|---|
| | | | |

Failures are defects, not notes. A pass here is evidence the route works on these devices; it does not approve the route, the retention period, the notice or the market.
