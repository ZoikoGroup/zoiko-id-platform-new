# Opening live Verify API verification for one market

Written 25 Nov 2026. This is the exact sequence a production verification session needs, what proves each step, and who must do it. It opens nothing by itself. It describes the gates already in the code; every one fails closed.

## What was rehearsed

The path from a company's go-live approval to a finished live check is exercised end to end by two PostgreSQL suites, run on 25 Nov 2026 with 12 tests passing:

- `backend/app/tests/component/test_verify_api_go_live_pg.py`: a company asks, a registry-verified check is required, a different person approves, the grant is signed, production credentials need a second person.
- `backend/app/tests/component/test_verify_api_live_pg.py`: a production session is refused before anything is created unless every gate holds; a customer reads the notice, agrees, is photographed and a match is verified with the lowest grade named; a poor match is a retake then inconclusive, never "not verified"; a decline ends the session with nothing photographed; a service problem spends no try.

Those tests inject their own policy, signer and acknowledgement. They prove the code path. They do not prove a deployment is configured or that any owner approved anything.

## The gates, in order, and who owns each

| # | Gate | Setting or action | Owner | If missing the API answers |
|---|---|---|---|---|
| 1 | The company is a registry-verified company (K2+ claim) | company verification in the console | the company, then an operator | `GO_LIVE_COMPANY_NOT_VERIFIED` |
| 2 | A production grant exists, approved by a different operator, signed | operator console, Verify API go-live tab; needs `ZID_VERIFY_GO_LIVE_KMS_KEY_ID` (a cloud KMS key). In a local stack with the local key provider a development key signs it. | operator, platform owner | `GO_LIVE_SIGNING_UNAVAILABLE`, then `PRODUCT_NOT_ENTITLED` |
| 3 | The grant names this country and method | the go-live request | company, approved by operator | `PRODUCT_NOT_ENTITLED` / market not in grant |
| 4 | A proofing policy is published for the country | a block for the country in `backend/config/idv/market-policies.yaml` (the file ships with none), or the built-in global policy with `ZID_IDV_GLOBAL_POLICY_ACK=accept-global-idv-policy` | the owners: Legal, Privacy | `MARKET_NOT_ENABLED` |
| 5 | The policy has a face-match route, a lawful basis and an impact assessment recorded | `biometric:` block in that policy | Privacy / DPO | `BIOMETRICS_OFF`, `MARKET_NOT_ENABLED` |
| 6 | The owners accept that the face route has no liveness test, no evaluated engine and no authenticity check | `ZID_IDV_FACE_ROUTE_ACK=accept-no-liveness-no-evaluation` | the owners, in writing | `MARKET_NOT_ENABLED` |
| 7 | A scanner and the imaging sidecar and face engine are reachable | `evidence_scanner`, imaging service, face lab | platform | `SCAN_UNAVAILABLE`, `FACE_ENGINE_UNAVAILABLE` |
| 8 | Production credentials | two-person approval in the console | company | `APPROVAL_REQUIRED` |
| 9 | A price | operator price record | operator, Finance | usage is counted but unpriced |

Gates 4 and 6 are legal acknowledgements. They are the owners' to give; nobody, including an engineer or this repository, sets them on the owners' behalf.

## Local rehearsal on a development stack

Use this to watch the flow by hand. It opens nothing real.

1. Start the stack as in the local-stack notes, with the cell running as the `zid_runtime` role.
2. Sign in as a company owner; create a sandbox application and run the Test Lab until the sandbox flow completes.
3. In the console ask to go live; as a different operator approve the request (the development key provider signs the grant).
4. Add a block for one country to a COPY of `market-policies.yaml` for the run, with a `biometric:` block that carries a placeholder lawful basis and impact reference marked `LOCAL-REHEARSAL`, and point the cell at it. Do not commit that file.
5. Set `ZID_IDV_FACE_ROUTE_ACK` in the local environment only, knowing it is a rehearsal and not an approval.
6. Create a production-application session for that country and open the launch link. The imaging service and face engine must be running for the photographs to be checked.

Delete the copied policy and unset the acknowledgement afterwards. A local rehearsal is never evidence for a deployment.

## What stays closed even after all nine gates

- The result is graded `FACE_MATCH_NO_LIVENESS` and says so. It does not claim document authenticity or liveness.
- Chip (NFC) reading, an independent PAD lab report and a document-authenticity engine are not part of this check. They need the engine, lab and provider work listed in the release-readiness register.
- No public statement about this capability may be made until the claims register approves it.
