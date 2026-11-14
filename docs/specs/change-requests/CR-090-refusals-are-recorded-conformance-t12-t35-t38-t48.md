# CR-090: refusals are recorded (conformance T12, T35, T38, T48)

Status: open (built and tested; T12 stays PARTIAL pending an owner decision).

## Why
ZID-RCG-003 section 12 cases T35, T38 and T48 were PARTIAL because the refusal happened but nothing recorded it; T12 was audited nowhere.
In each place the refused request's transaction ends in an error, so the record is written in its own transaction after it.

## What changes
- T35: a provider callback refused for a bad signature, staleness or a replay with a different body (`CALLBACK_SIGNATURE_INVALID`,
  `CALLBACK_STALE`, `CALLBACK_CONFLICT`) writes a DENIED `verification.callback.rejected` audit record and raises one mandatory HIGH alert
  (existing alert store; repeats fold into one alert per adapter and code). Ids and the code only; never the body or any signature.
  The response to the provider is unchanged. Not covered: Stripe's payment webhook (no tenant to record against).
- T38: `POST /v1/evidence/packages` and `/v1/evidence/bulk-exports` no longer require the purpose in the request schema (`purpose` defaults to
  empty). A missing or blank purpose after the authority check is refused with 422 `EXPORT_PURPOSE_REQUIRED` and recorded as a DENIED
  `evidence.export.refused` audit record (who asked, which resource, mode, reason). Contract files regenerated.
- T48: a refused `POST /v1/credentials/disclosures/{grant_id}:read` (purpose, consumer or recipient mismatch, grant not in force, and the
  other refusals) writes a DENIED `credential.disclosure.denied` audit record with the grant, check, consumer, the purpose the consumer
  stated, and the code; never the recipient or any claim value. Status codes unchanged.
- T12: a request for a claim's documents that RLS makes invisible (another organization's, or none) is audited in the caller's own
  organization as DENIED `organization.kyb.proof_access_denied` / `CASE_NOT_VISIBLE`. The answer stays the same 404.

## Not in this change
- T12 stays PARTIAL: the case states 403; the platform answers 404 so that nothing confirms another organization's claim exists. Owner to
  decide whether 404 satisfies the case.
