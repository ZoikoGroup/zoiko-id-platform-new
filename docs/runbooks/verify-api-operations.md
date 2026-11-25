# Verify API operations (partner platform)

**Source:** ZID-EXT-IDV-001 section 15, epic E20, journey J5. **Built:** CR-160. **Status:** operating guide for the sandbox and for the production gate; the live check engine is not connected (owner decisions D03, D04), so no production verification is served yet.

This is a guide, not a numbered runbook: it is not in the service catalogue and no alert rule pages from it. The availability and latency targets (decision D13) are the owner's to ratify; the thresholds below are starting points to tune against real traffic, not promises.

## What the service reports

Counters in the cell's metrics endpoint (`zid_telemetry` registry). The platform refuses a tenant, session, client or address label on any metric, so these carry only the environment, the outcome and the kind of event. For one company's numbers use its usage screen or the operator's case tools, never a metric label.

| Metric | Labels | Read it as |
|---|---|---|
| `zid_verify_api_sessions_total` | `environment`, `event` = started / decided / closed | Volume, and how many sessions end without a decision (closed). |
| `zid_verify_api_decisions_total` | `environment`, `outcome` | Final results by outcome. A sudden rise in `inconclusive` means a provider or capture problem, not fraud. |
| `zid_verify_api_auth_total` | `method` = secret / private_key_jwt / bearer, `result` = issued / denied / ip_denied / rate_limited / error | Token requests and bearer checks. |
| `zid_verify_api_cases_total` | `kind`, `event` | Disputes, appeals and erasure requests moving through review. |
| `zid_verify_api_go_live_total` | `event` = approved / declined / revoked | Production gate activity. |

Webhook delivery health (success, lag, dead letters) is the webhook module's own metrics and its delivery log; the Verify API uses that module unchanged.

## Starting alert thresholds (tune, then ratify under D13)

- `denied` plus `ip_denied` on `bearer` or a token method rising sharply over a baseline: credential guessing or a leaked credential. Go to "A credential may have leaked".
- `rate_limited` above zero for more than a few minutes: someone is hammering one client ID. The limit is per client ID, so a legitimate client is only affected if its own secret is wrong.
- `closed` growing faster than `started`: sessions expiring or being cancelled in bulk; check the partner's integration first.
- `error` on any method: a fault in the cell, not a partner problem. Treat as a service incident (RB-01, RB-02).
- `zid_telemetry_cardinality_overflow_total` rising: a label is being fed an unbounded value. These metrics only take fixed words; find and fix the caller.

## A credential may have leaked (journey J5)

Use the operator console, in this order. Every step is recorded.

1. **Stop new production sessions for the company at once:** Commercial > Verify API go-live > "Stop production for a company". This ends the grant, suspends every production application and ends every token it issued. It does not touch the sandbox.
2. **Stop one application only:** the company can suspend the application, or an operator can use the same stop for the whole company. A suspended application's tokens stop immediately.
3. **Replace the credential:** the company rotates it (a secret or a key credential), or revokes it outright; revoking ends every token from that credential at once. For a leaked private key, register a new public key and revoke the old credential: there is no secret held by us to rotate.
4. **Webhooks:** retire the company's compromised endpoint from its Webhooks screen. Replays of events are available only after the incident review, from the delivery log.
5. **Preserve evidence:** do not delete anything. Sessions, decisions, the grant and its signature, the case history and the audit trail stay as they are. Take the time window from the `auth_total` counters and the access log, not from any label.
6. **Tell people:** follow RB-17 if there is any sign another company's data was reachable (that is a SEV0). Otherwise notify the company's security contact and record the incident.
7. **Reopen:** the company asks to go live again through the normal gate; the operator approves it as a new request, with a different person from the reviewer.

## A customer asks to be forgotten

The company raises an **erasure** request (Developers > Disputes, appeals and erasure) with the customer reference exactly as it sent it. An operator takes it into review; a different operator resolves it. Resolving removes the reference and the free-form details from every session of that company that carries it, and records how many sessions changed. Results stay, because they hold no personal data and the usage record and audit trail depend on them.

- The request keeps only a fingerprint of the reference, scoped to the company. Do not ask the company to paste the reference anywhere else.
- If a legal hold applies, **reject** the request with a note saying so; the company reads it. Do not resolve it.
- Erasure removes what the Verify API holds. It does not reach the company's own systems, and it does not touch a person's own Zoiko iD identity; those are separate rights requests.

## A company disputes or appeals a result

Cases never change a result. Take it into review, read the company's reason and the session's decision history, then resolve with a note or reject with a note. If a new look is warranted, tell the company to ask for a **recheck**: that creates a new decision version and the original stays on record, marked as superseded.

## Not covered here

Per-region availability and latency targets, provider health for the live engine, and capacity numbers wait for the owner decisions (D13) and for a connected engine. Until then these counters describe the sandbox and the gate.
