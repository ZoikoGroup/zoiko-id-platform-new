# CR-151: Verify API, slice 3: the person's hosted test page and the console Test Lab

Status: slice 3 built 24 Nov 2026 (uncommitted). Source: `ZID-EXT-IDV-001`, WF-09/WF-10 (hosted flow), epics E05 and E11/E12 (console Sessions and Test Lab).

**Why.** Slice 2 (CR-150) made sessions and a launch link, but nobody could open the link and no one in the company could try the journey without writing code. This slice gives the customer a page at the link and gives the company a Test Lab inside the console.

**The person's page** `/verify/session/{token}` (web app, no sign-in). It opens the link, tells the person who is asking and why in plain words, says clearly that this is a test with nothing real checked, and lets the tester choose the answer (pass, fail, needs a reviewer, no answer, ran out of time). Then it thanks them and, if the
application registered a return address (https only), offers a link back. A wrong, used, expired or replaced link reads the same single message. The page never shows the company's own reference for the person or the session id. Real capture (camera oval, document photo) arrives with the real engine.
A same-origin relay `/api/verify-hosted/{token}` (checks the link's shape, the request origin, and allows only `open` and `simulate` with a short outcome word) passes calls to the cell.

**The company's Test Lab** (Developers page, inside an application): "Make a test verification" creates a sandbox session, shows its link once (it is kept only as a fingerprint), and lists the application's sessions with a plain-words state, refreshing every 8 seconds. Backed by two console routes behind the PDP:
`GET /v1/verify-api/applications/{id}/sessions` (`verify_api.application.read`) and `POST /v1/verify-api/applications/{id}/test-sessions` (`verify_api.application.manage`). They use the same session service and rules as the partner's server, acting as the application; a suspended application cannot make one.

**Tests.** Web: `verify-hosted/logic.test.ts` (4), `test-lab-logic.test.ts` (2), tsc 0 errors, eslint clean. Backend component test for the console routes (create, list, status filter, link works for the person, unknown application, bad body, suspended, no sign-in). Contract regenerated.

**Known limits.** The cell's limiter for wrong links counts by the address that reaches it; behind the web relay that is the web server, so it limits guessing overall rather than per person (forwarding the person's address needs the platform's trusted-proxy setting, a later hardening). Not yet: partner webhooks, production gate, usage billing, real capture and engine, SDKs, console screens for production access.
