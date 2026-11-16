# CR-094: API agent credential path: a signed one-time assertion for a short-lived token (ZID-MACHINE-001 §9, §16)

Status: **applied, on the engineering owner's instruction of 2026-11-14 (option 1 of the two offered). The security owner has not yet signed the design: the open decisions are listed at the end.** It completes CR-093, which stopped before the agent had any credential.

## Why

CR-093 let an agent exist with an API client in place of a container image. The client had no way to act: nothing turned its private key into a credential for the agent. The platform never sees the private key and must not issue a token on the client's say-so.

## The exchange

`POST /v1/machine/api-agent-token` with `{"assertion": "<compact JWS>"}`. No bearer: the signature is the credential (a PROTOCOL route in the route map, like the workload token).

The client signs, with a key whose public half was registered and approved in CR-093, a JWS of type `api-client-assertion+jwt` (ES256 or Ed25519) with: `iss` and `sub` = its client id; `aud` = the platform's exact token address (`<issuer>/v1/machine/api-agent-token`); `resource` = one audience the client was registered with, exactly; `tenant_id`; `jti` (single use); `iat` and `exp` at most 60 seconds apart.

| Step | Rule |
|---|---|
| 1 | The company and client named in the unverified assertion only choose where to look. Nothing in it is trusted yet. |
| 2 | The signature must verify against the PUBLIC keys stored with the approved build. A header carrying its own key (`jwk`, `jku`, `x5u`) is refused. |
| 3 | Issuer, audience, registered resource, one-minute lifetime and the single-use id are checked exactly. |
| 4 | The assertion id is burned (`agent.api_assertion_jti`, row-level security) BEFORE the verdict, so a replay fails even when the first use was refused. |
| 5 | The agent must be ACTIVE and not frozen (agent, ancestor or whole company), its binding live and unexpired, its build approved and not revoked, and its release unchanged. |
| 6 | The attestation an API client can show is capped at `API_ATTESTATION_CAP` (1). The gate holds it against the floor of the risk class the token asks for (`API_TOKEN_RISK_CLASS`, 1). The cap is now read by code. |
| 7 | Evidence is written for the issue (`agent_registry.api_credential_issued`) and for every refusal after the signature verified (`..._refused`, with the codes). |

Failures before the signature verifies all return one answer (401 `ASSERTION_INVALID`), so the route does not reveal which clients exist. After it verifies, the refusal names the reason (403).

The token: subject = the agent principal; scope `workload` (what the tool gateway already requires); risk class 1; at most 5 minutes; sender-constrained to the signing key (`cnf.jkt`, DPoP). Every later call is still governed as for any agent: authority, mandate, policy, governance gates and the CR-093 tool cap. A suspended or revoked agent's token stops working as for any agent.

## Amendment (16 Nov 2026): what the token is worth when it is used

Found by driving the whole path over HTTP: an API agent could sign in but no tool call could ever be granted. The token carries no identity session, so the
request was assurance level Z0, and even risk class 1 needs Z1 (`AUTHN_FLOOR`). Every call was refused with `ASSURANCE_INSUFFICIENT`.

Rule, in `session/application/authenticate.py` (`_is_api_agent_credential`): a request counts as **Z1** when, and only when, all three hold:

1. the token is one this platform minted for an API agent (`client_id` begins `api-agent:`, signed by the platform's own key ring);
2. its scope is `workload`;
3. the request carried a DPoP proof that verified against the key the token is bound to (a fresh proof of possession on this request, single use).

Z1 clears the R0 and R1 floors and nothing else. R2 needs Z2 and R3 needs Z3; a key proof never reaches either (`test_api_agent_assurance.py`). No header the caller sets
is read. The token's own risk class (1) and the tool ceiling (risk class 1, side effect NONE or REVERSIBLE) are unchanged, and the call still needs an authority grant
from a person, a policy permit and the governance gates. Without the grant the gateway refuses and names the reason (`POLICY_NO_PERMIT`).

How a person gives the grant: `POST /v1/authority-grants` with `grant_type: AGENT_DELEGATION`, delegated from a grant the person already holds with delegation allowed;
narrow actions and resources, not delegable further, a short window. A person cannot give what they do not hold.

## What it does not do

- It issues no token for a key the build does not hold, for an agent that is not ACTIVE, or for a binding that is revoked.
- It does not widen anything: tool cap, governance, freeze and kill paths are unchanged.
- It stores no private key, no assertion and no token. Only the id of an assertion, its binding and its expiry are kept. The runtime database role has no DELETE right, so an id stays burned for good; clearing rows long past expiry is a retention job for the data owner.
- The gateway does not read an attestation level from the token. The CR-093 tool ceiling is now risk class 1, the same as the token class, so the two caps agree. There is no action-catalogue entry on purpose: the signed assertion is the credential, as with the workload token, and a catalogue action would imply a person-held permission that does not exist.

## Owner decisions still needed

1. May an API-only agent hold a credential at all, and is five minutes the right lifetime?
2. Should the tool ceiling fall to risk class 1 for credentialed API agents, matching the attestation cap (today it stays 2)?
3. Should a client be allowed more than one live assertion per second, or a per-client rate limit (none exists)?
4. Is a verified DPoP proof of the agent's own key on the request an acceptable basis for assurance Z1, limited to R0 and R1, as in the amendment above? If not, the alternative is to mark the one or two read-only tool actions `machine_callable` (the CR-066 mechanism) and leave the assurance rule alone.

## Evidence

`backend/app/tests/unit/test_api_agent_token.py`, `backend/app/tests/component/test_api_agent_token_pg.py`, `backend/app/tests/unit/test_api_agent_assurance.py`, and `backend/app/tests/component/test_agents_api_client_pg.py` (the whole lifecycle over HTTP only: register, two-person approval, bind, activate, sign in, a person delegates authority, a governed tool call with a DPoP proof, replay and unbound-token refusals; also over a real socket). Operator command: `tools/scripts/agents_api_check.py --create-api-agent` (see `docs/runbooks/agent-go-live.md`).

## Sign-off (UNSIGNED)

| Role | Name | Date | Decision on the four questions above |
|---|---|---|---|
| Security owner | | | |
| Platform owner | | | |
