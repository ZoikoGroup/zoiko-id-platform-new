# Admin console: manual test checklist (Administration menu)

Traced from the code on 2026-11-20 (branch `akarsh`). Customer console `http://localhost:3000`, operator console `http://localhost:3100`.
Every page lists: what you need first, the fields with a valid sample, the button and what should happen, and wrong inputs to try.

## 0. Before you test anything

| Need | Why / how |
|---|---|
| A workspace that is **verified** and you are **OWNER** | Most admin calls check your *authority grants*, not just your role. A fresh sign-up gets "Not permitted" everywhere. Shortcut: `python tools/scripts/grant_local_admin.py --tenant-id <id>` (or use the tenant seeded by `bootstrap_local.py`). |
| A **second team member** (and for some flows a third) | Almost every approve / review / sign step refuses the person who made the request. Invite one from **Team & invitations**, open the link in another browser profile. Give them APPROVER through an approval request. |
| A passkey on your account | R3 actions ask for passkey step-up. |
| `seed_local_sanction_lists.py` run recently | Screening answers "unavailable" without it. |

What is **not enforced locally** (so a refusal you expect may not happen):
- Role checks on the server are **report-only** (`role-policy.yaml`: `route_matrix_enforcement: REPORT_ONLY`). Test role limits by checking that **buttons are hidden**. Real refusals come from grants ("Your authority grants do not cover this action in this tenant").
- Plan limits: `ZID_COMMERCIAL_ENFORCEMENT=shadow`, so they are logged, not blocked.
- Passkey step-up: shadow by default.
- Agent governance: `ZID_AGENT_GOVERNANCE=shadow`. Refusals show as "Would be refused".

Reusable test values:
- **Public key set (JWKS)** (RFC 7517 example, public only):
  `{"keys":[{"kty":"EC","crv":"P-256","kid":"1","x":"MKBCTNIcKUSDii11ySs3526iDZ8AiTo7Tu6KPAqv7D4","y":"4Etl6SRW2YiLUrN5vfvVHuhp7x8PxltmWWlbbM4IFyM"}]}`
- **Private-key negative:** the same JWKS with an added `"d":"abc"` → "This contains a private key…".
- **Principal ID (UUID):** copy it from People & principals or Team.

---

## 1. Identity providers: `/admin/identity-providers`

Tabs: **Providers**, **Trust anchors**. You need OWNER/ADMIN and the `tenant.identity_provider.change` grant. Saving asks for your passkey.

### Add provider ("Add provider" → "Connect your company sign-in" → **Connect provider**)

| Field | Valid sample (OIDC) | Rule / error |
|---|---|---|
| Which sign-in does your company use today? | Another OpenID Connect provider | Options: Entra ID / Okta / Google Workspace / OIDC / SAML 2.0 |
| Name people will see | `Sign in with Acme` | "Display name is required." |
| The provider's address (issuer) | `https://login.acme-test.com` | "Issuer is required."; server: must be `https://` |
| Client ID | `zoiko-id` | "Client ID is required." |
| Details → Sign-in address | `https://login.acme-test.com/authorize` | "Endpoints must be https:// URLs." |
| Details → Token address | `https://login.acme-test.com/token` | "Token endpoint is required." |
| Details → Keys (JWKS) | JWKS above | invalid JSON / no `kty` / private key → error |
| Advanced → Who may sign in | Only people who already have an account | or "Anyone from my company" (JIT) |
| How Zoiko iD proves itself | A signed key (recommended) | "A shared secret" also needs "Name of the stored secret" |
| Scopes | `openid, email, profile` | one word each, no spaces |
| How should we learn details | Pinned settings | Trust anchor mode needs an active anchor |
| Valid until | default (+180 days) | must be in the future, ≤400 days |
| Accepted signing algorithms | ES256, RS256 | "Choose at least one signing algorithm." |
| Assurance levels (optional) | `{"urn:example:mfa": 2}` | numbers ≥0 |
| Approval request ID | blank | needed only if your policy demands a second admin |

SAML instead of OIDC:
- Metadata address: `https://idp.example.com/metadata`
- Provider's entity ID: `https://idp.example.com/saml`
- Single sign-on address: `https://idp.example.com/sso`
- Signing certificates: a PEM certificate. Make one with `openssl req -x509 -newkey rsa:2048 -nodes -keyout k.pem -out c.pem -days 365 -subj "/CN=idp.example.com"`, then paste `c.pem`.
- Your entity ID: default `http://localhost:8000/saml/sp`.

**Expected:**
- OIDC: toast `Integration "Sign in with Acme" created`. The row shows OIDC, **Active**, Config rev 1.
- SAML: a "Provider connected" box showing the ACS URL and the SP entity ID.

**"Find the rest for me"** (discovery) only works with internet access. The Google Workspace preset (`https://accounts.google.com`) succeeds with "Found it…". A made-up host gives "We could not find the details automatically".

| Negative test | Expected |
|---|---|
| Same issuer added twice | `FEDERATION_ISSUER_EXISTS` |
| Issuer `http://login.acme-test.com` | `VALIDATION_FAILED` "an OIDC issuer is an https:// URL" |
| Endpoint `https://10.0.0.1/authorize` or `https://foo.local/x` | "use a DNS name, not an IP literal" / internal host refused |
| JWKS with `"d"` | private key error |
| No algorithm ticked | "Choose at least one signing algorithm." |

**Row actions:**
- Copy sign-in link.
- **Edit settings** → "… updated (revision N)".
- **Rotate trust material** → "Trust material rotated (revision N)".
- **Disable** → "… quarantined". Status becomes **Quarantined**, and Edit/Rotate are disabled.
- **Enable**.
- SAML only: **Switch on IdP-initiated SSO**. It needs Landing targets (1–10 exact https URLs) and an Approval request ID.

### Trust anchors ("Add trust anchor")

| Field | Sample | Error |
|---|---|---|
| Entity ID | `https://anchor.example.org` | must be https |
| Pinned JWKS | JWKS above | needs a `"keys"` array |
| Allowed domains | `example.org` | 1–20 plain DNS names |

- **Expected:** "Trust anchor … added", Status **Active**, Pinned keys 1.
- Adding the same anchor again → `TRUST_ANCHOR_EXISTS`.
- Disable / Enable work as for providers.

**Local caveat:** the bundled Keycloak (`docker compose --profile idp up -d`, `http://localhost:8180`, realm `acme`, client `zoiko-id`, users `alice`/`alice-demo-only`) **cannot be connected**. The page requires https on port 443/8443 with public DNS. Creating a provider with made-up https addresses works, because creation only checks the format. A real sign-in through it will fail.

---

## 2. Provisioning (SCIM): `/admin/provisioning`

You need `provisioning.source.manage`. Registering is R3 (passkey).

### Register source ("Register source")

| Field | Sample | Rule |
|---|---|---|
| Display name | `Workday HR` | ≤128 characters |
| OAuth client | "Create the connector's OAuth client (paste its public keys)" | or "Use an existing OAuth client ID" |
| **Make a key pair for me** | press it → **download/copy the private key now** | — |
| Public keys (JWKS) | auto-filled | 1–5 keys, each with a `kid`, no private key |
| Approval request ID | blank | — |

**Expected:**
- Toast "Provisioning source registered".
- A box "Workday HR is active" showing the SCIM base `http://localhost:8000/scim/v2`, the token endpoint, the client ID and the source ID.
- The row shows **Active**.

| Negative test | Expected |
|---|---|
| Existing-client mode with an unknown client ID | `PROVISIONING_CLIENT_INVALID` |
| A client already used by a source | `PROVISIONING_SOURCE_EXISTS` |
| A JWKS key without `kid` | "Each key needs a kid…" |

**Suspend:**
- Toast "Provisioning source suspended".
- SCIM calls then return 403 "no active provisioning source".
- **Resume** asks for a passkey, then "…resumed".

### Send a real SCIM request (proves the source works)
1. Check the source can sign in:
   `python tools/scripts/sync_auth0_users.py --check --scim-client-id <client id> --scim-key <private-key.json>`
2. Get a token with `client_credentials`, scope `scim:provision`, and a `private_key_jwt` assertion. The script above shows how.
3. Create a user:
   ```
   curl -X POST http://localhost:8000/scim/v2/Users -H "Authorization: Bearer $T" -H "Content-Type: application/scim+json" \
     -d '{"schemas":["urn:ietf:params:scim:schemas:core:2.0:User"],"userName":"jane@acme.test","externalId":"hr-001","active":true}'
   ```
   Expect **201**. The person appears in People & principals.
4. Negative: leave out `userName` → 400 "userName is required".
5. Negative: a token without the scope → 403 "scope scim:provision is required".

### Directory sync ("Connect Auth0")
Fields:
- Name: `Auth0`.
- Provisioning source: the one created above. With none: "Register a provisioning source first".
- Auth0 domain: `dev-acme.us.auth0.com`. Must end in `.auth0.com`.
- Client ID: at least 8 characters.
- Client secret.
- How often: Every hour.

**Expected:** "Directory connected".
- A second sync on the same source → `SYNC_EXISTS`.
- **Run now** → "Queued…", at most once per 60 s.
- With fake credentials, "Last run" shows "Auth0 refused the client ID or secret". A real run needs a real Auth0 tenant.

---

## 3. Authority & approvals: `/admin/authority`

Tabs: **Grants**, **Approvals**.

### Issue grant ("Issue grant")
You need OWNER/ADMIN/OPERATOR, **and an ACTIVE grant of your own with a root assertion**. The form copies "On behalf of" and the root assertion from it.

| Field | Sample | Rule / error |
|---|---|---|
| Grantee | second member, picked from the list | "Choose the grantee principal." |
| Value limit / Currency | `1000.00` / `GBP` | "…non-negative decimal, e.g. 2500.00." / 3-letter code |
| Actions | tick `payment.release`, `mandate.create` | "List at least one action." |
| Resources | tick `payment:*`, `mandate:*` | "List at least one resource." |
| More options → On behalf of | The company (pre-filled) | — |
| Authority root assertion | pre-filled | needed for a top-level grant |
| Derive from my grant | None (top-level) or your grant | must be a grant UUID |
| Grant type / Continuity | ROLE_ISSUED / PERSON_BOUND | — |
| Jurisdictions | `GB` | empty = any |
| Count limit | `10` | whole number |
| Minimum assurance | No minimum | AAL-Z1..3 |
| Valid from / until | defaults (now → +90 days) | "The end of the window must be after its start." |
| Grantee may delegate further + depth | off | depth 1–16 |
| Risk class | R3 | R0–R3 |

**"Review before issuing"** appears for wildcards (`payment:*`), for no value and no count limit, or for depth >1. Tick "I have reviewed this scope…". Otherwise: "This grant is broad…".

**Expected:**
- Toast "Grant issued". The panel shows status **Active**.
- Authority chain section: "Valid at R1".

| Negative test | Expected |
|---|---|
| Derive from your grant, add an action or a higher value than the parent | `GRANT_SCOPE_WIDENING` ("A limit cannot give more than the grant it comes from") |
| Derive from a parent that doesn't allow delegation | `GRANT_DELEGATION_NOT_ALLOWED` |
| Parent depth 1, child delegates at depth 1 | `GRANT_DEPTH_EXCEEDED` |
| Root assertion replaced by a random UUID | `AUTHORITY_MISSING` |
| End before start | client error |

**Revoke** ("Revoke this grant?" → **Revoke grant**):
- Toast "Grant revoked". The badge shows **Revoked**.
- A child grant now shows "Not valid", `AUTHORITY_REVOKED`.
- Revoking again from an old tab → `GRANT_INVALID_TRANSITION`.

### Approvals tab
**New request (advanced):**

| Field | Sample |
|---|---|
| Subject | `payment:pay_123` |
| Transaction context: Action / Resource / Change | `payment.release` / `payment:pay_123` / blank. Or switch on "Enter the exact string". |
| Approval mode | Single (also All of, K of n, Role pair, External confirmation) |
| Approving roles | `payment.release` (an action that the approver's grant covers) |
| k (K of n only) | 1–16 |
| Expires after | 1 hour |

**Expected:** "Request opened", with the request ID, state **Pending** and the context. **Copy the ID and the exact context string.** The server keeps only a hash, so you can't see the context again.

**Decide** (as the **second member**):
- Fields: Request ID, Approve/Deny, "Acting under my grant", the same transaction context.
- **Expected:** "Decision recorded: Approved". The list changes from Pending to **Approved**.

| Negative test | Expected |
|---|---|
| Requester decides own request | warning "Self-approval is not allowed", button disabled; forced → stays Pending `APPROVAL_REQUIRED` |
| Wrong transaction context | state **Stale** `APPROVAL_STALE` |
| Same approver votes twice | `APPROVAL_DUPLICATE_OR_MISMATCHED` |
| Acting under someone else's grant | `APPROVER_GRANT_NOT_HELD` |
| Role pair with one role | `VALIDATION_FAILED` |
| Wait past expiry | **Expired** |

### Delegation templates (Team & invitations → **Delegations** tab)
"New delegation template":
- Name: `Supplier payments up to 5,000`. Must be 2+ characters.
- Actions: `payment.release`.
- Resources: `payment:*`.
- Value: `5000`. Currency: `GBP`.
- Lasts at most: `30` days (1–365).

**Expected:**
- "Template saved". The row shows **Active**, "5000 GBP", "30 days".
- **Retire** → "Template retired".

Negative tests:
- Days 0 or 366, name `A`, or currency `GB`: **Save stays disabled with no message.** This is a UX defect.
- A duplicate name → `TEMPLATE_EXISTS`.
- There is **no Apply button**. The API exists, but the UI doesn't use it.

---

## 4. Mandates: `/admin/mandates`

You need an **ACTIVE grant that you hold and that includes `mandate.create`** (issue one in §3 from the other owner). Otherwise the banner "You can review and switch mandates on or off, but not create them" appears.

### Create mandate

| Field | Sample | Rule / error |
|---|---|---|
| Source grant | your `mandate.create` grant (auto if only one) | "Choose the source grant." |
| Executor | **yourself** (so you can test reservations), or an AI agent | "Choose the executor." |
| Purpose ID | keep the generated UUID | must be a UUID |
| Risk class | R3 | — |
| Allowed actions / Resource scopes | `payment.release` / `payment:*` (or "Use the grant's actions and resources") | ≥1 each, ≤64 |
| Jurisdictions | `GB` | optional |
| Currency / Total / Per-transaction | `GBP` / `500.00` / `50.00` | decimal; currency needed with values |
| Count limit | `5` | whole number |
| Valid from / until | defaults (+30 days) | end after start |
| Executor may sub-delegate | off | depth 1–16 |

**Expected:**
- "Mandate created; activate it when ready". Status **Pending approval**.
- A Live balance card appears, refreshing every 10 s.

| Negative test | Expected |
|---|---|
| Total higher than the grant's value, an extra action, or ending after the grant ends | `GRANT_SCOPE_WIDENING` |
| Per-transaction `600` with total `500` | `MANDATE_BUDGET_INVALID` |
| Source grant revoked | `MANDATE_SOURCE_INVALID` / `GRANTOR_AUTHORITY_MISSING` |

### Status buttons
Allowed moves:
- Pending → Activate / Revoke
- Active → Pause / Revoke
- Paused → Activate / Revoke

Who sees the buttons:
- Activate: OWNER/APPROVER.
- Pause / Revoke: OWNER/ADMIN.

Results:
- **Activate** (choose the risk class) → "Mandate is now active".
- **Pause** → "…paused".
- **Revoke** → "…revoked", and no buttons remain.

Negative tests:
- Revoke the source grant, then Activate → `MANDATE_SOURCE_INVALID`.
- A move from an old tab → `MANDATE_INVALID_TRANSITION`.

### Reservations (shown only when Active; "Operator and testing tool")
**Only the executor can reserve.** Anyone else gets `MANDATE_EXECUTOR_MISMATCH`.

| Step | Input | Expected |
|---|---|---|
| Reserve | Action `payment.release`, Value `40.00`, Count `1` | "Budget reserved", row **Reserved**, balance Reserved 40 |
| Commit | tick "Commit-time recheck passed" | "Reservation committed" |
| Commit unticked | — | `AUTHORITY_REVOKED_DURING_EXECUTION` |
| Release | — | "Reservation released" |
| Value `60` (per-transaction limit 50) | — | `MANDATE_VALUE_LIMIT` |
| Running total over 500 | — | `MANDATE_VALUE_LIMIT` |
| 6th use (count limit 5) | — | `MANDATE_COUNT_LIMIT` |
| Commit after expiry | — | `RESERVATION_EXPIRED` |

---

## 5. Policies: `/admin/policies`
- **Draft / validate / simulate:** OWNER or ADMIN.
- **Review:** someone who is **not the author**.
- **Publish:** an **APPROVER**. OWNER is not enough.

1. **New release:**
   - Policy name: `payments`. Must match `^[a-z][a-z0-9-]{1,62}$`. **An invalid name makes Save draft silently do nothing.** This is a defect.
   - Cedar: keep the template.
   - Test cases: keep the two template cases.
   - Expected: "Draft created", status **Draft**, Reviewers "0 of 1".
2. **Validate:** "All checks passed: ready for review", and the status moves to **Review**.
3. **Simulate:** request JSON. Shows "This release" next to "Active release" as Allow/Deny.
4. **Approve as reviewer** (the second person) → "Review recorded".
5. **Publish** (APPROVER) → "Release published", status **Active**.

| Negative test | Expected |
|---|---|
| Set the first case to `"expect_allow": false` | `POLICY_TEST_FAILED` |
| Cedar `permit (principal action` | `POLICY_PARSE_ERROR` |
| Author approves as reviewer | `POLICY_REVIEWER_IS_AUTHOR` |
| Publish without a review | `POLICY_REVIEW_REQUIRED` |
| Test cases `{}` | "Give at least one allow and one deny case." |
| Another person edits your draft | `POLICY_AUTHOR_ONLY` |

## 6. Access decisions: `/admin/decisions`
Any role can use this page.

| Field | Sample |
|---|---|
| Who is asking | yourself |
| What do they want to do | Release a payment |
| Applies to: kind / name | `payment` / `inv-001` |
| Amount / Currency / Spending limit | `300.00` / GBP / your active mandate |
| Advanced → Extra context | "Insert template" |

**Expected:**
- An outcome badge (Allow / Deny / Step-up / Approval required), reason codes, decision ID, policy release and obligations.
- For a payment the result is usually **Step-up**. Press "Confirm with passkey and ask again".

Negative tests:
- A person with no grant → Deny `AUTHORITY_MISSING`.
- An amount above the mandate → Deny.
- Context `[]` → "Context must be a JSON object."
- `"risk_class":"R9"` → `VALIDATION_FAILED`.

## 7. Evidence & audit: `/admin/evidence`
Tabs: Receipts, Bulk exports, Audit & integrity, Legal holds, Cases, Provenance, Keys & identity.

**Receipts:** open a receipt.
- **Verify hash, ledger and checkpoint** → "Integrity verified" (or "not yet sealed by a checkpoint").
- **Replay decision** → "verified match".
- **Show inclusion proof** → JSON.

**Export a signed package:**
- Mode: Auditor. Purpose: `Customer audit request` (3+ characters). Case reference: `CASE-2026-014`.
- Expected: "Signed evidence package created", and `evidence-<id>.json` downloads.

**Annotate:**
- Kind: Annotation. Reason code: `DUPLICATE_SUBMISSION` → "Annotation added beside the receipt".
- Negative: `dup` → "Use capital letters, digits and underscores…".

**Bulk export:**
- Fields: Receipt IDs (UUIDs, ≤100), Mode, Purpose, Case ref, Window (1 h / 24 h / 7 d).
- Expected: "Bulk export created". The row shows **Available** and the time left.
- Negative: `abc` → "Not a receipt ID: abc". 101 IDs → error. Downloading after expiry → `EVIDENCE_EXPORT_EXPIRED`.

**Legal hold** (two-person rule):
1. Under Authority → New request, with the exact string context `evidence.legal_hold.manage|legal_hold:*|MATTER-001||verification.document`.
2. The second member approves it.
3. Apply a legal hold:
   - Legal basis: `Litigation hold`.
   - Matter: `MATTER-001`.
   - Object types: `verification.document`.
   - Review date: +90 days.
   - Approval request ID: the approved request.
4. Expected: "Legal hold applied", status **Active**.

Negative tests:
- Approval made for a different reference → `APPROVAL_REQUIRED`.
- Self-approved → `APPROVAL_REQUIRED`.
- The requester releases their own hold → `EVIDENCE_ACCESS_DENIED`.

## 8. Privacy & data rights: `/admin/privacy`
**Rights request:**
1. Record it:
   - Type: Access.
   - Subject principal ID: your own (36 characters; otherwise "Record" is disabled).
   - Pack: `uk-gdpr`.
   - Expected: "Request recorded" with a deadline badge.
2. Then:
   1. **Mark verified**.
   2. **Execute fulfilment**.
   3. Acknowledge with a reference of 3+ characters.
   4. **Close request** with outcome Completed.

Negative tests:
- Fulfil before verifying → `PRIVACY_VERIFICATION_REQUIRED`.
- Any action on a closed request → `PRIVACY_INVALID_STATE`.

**Retention policy** (needs an approved request first):
- Fields: Code `VERIF_DOCS_365`, Object type `verification.document`, Trigger CREATED_AT, `365` days, CRYPTO_SHRED.
- Approval context: `privacy.retention.manage|retention_policy:*|VERIF_DOCS_365|verification.document|365|CRYPTO_SHRED|None`. A blank backup window is written as the literal word `None`.
- Negative: 40000 days → `VALIDATION_FAILED`. The creator approves → `PRIVACY_APPROVAL_REQUIRED`.

**Processor:**
- Fields: ID `email-provider`, Name `Acme Mail Ltd`, Locations `GB, IE`, Mechanism `UK_IDTA`, SLA `30`, data class `D2`, Contract `DPA-2026-01`.
- A **different person** approves it.
- Negative tests:
  - ID `Email Provider` → Register is disabled.
  - Role Processor with no contract → `PRIVACY_SCHEMA_INVALID`.
  - Self-approve → `PRIVACY_APPROVAL_REQUIRED`.

**Notice:**
- Fields: Code, Audience `data_subjects`, Text (20+ characters).
- Steps: **Prepare for approval** → approve → **Publish notice**.
- Editing the text after preparing resets the approval.

The other tabs (DPIA, Breaches, Erasure, Models, Biometric, Assisted) were not traced field by field.

## 9. People & principals: `/admin/accounts`
**List:** filter by Type and Status, or search by principal ID. Click a row to open it.

**Revoke all sessions** (passkey) → "N sessions signed out".

**Open assisted recovery** on **another** person:
- Shows a one-time "Recovery handle".
- "Done" stays disabled until you tick "I have passed the handle…".
- Doing it on yourself → `RECOVERY_NOT_ALLOWED`.

**Deny a case:** Case UUID + Reason (3–200 characters) → "Recovery case denied".

**Lockbox** ("Place under lockbox"):
- Fields: 2–5 approver UUIDs, Threshold k `2`, Cooling-off `168` h (168–720), Approval request ID.
- The banner shows the exact approval string, e.g. `lockbox:k=2:h=168:<sorted ids>`.
- Client errors:
  - "Approvers must be distinct."
  - "The account holder cannot approve their own recovery."
  - "Cooling-off must be 168–720 hours."

## 10. Operator access: `/admin/operator-access`
**Requests table:**
- Empty unless a Zoiko operator raises a request ("No operator access requests").
- On a request: Approve / Deny / Withdraw, with an optional reason ≤2000 characters.

**Policy:**
- Switch "Require my approval (lockbox)…". Turning it off shows a warning.
- Jurisdictions: `GB, IE`.
- **Save policy** → "Operator access policy saved", and the revision goes up by 1.
- Negative: `GBR` → `OPERATOR_SCOPE_INVALID`.

## 11. Residency: `/admin/residency`
**Change profile:**
- Fields: Home cell `local-1`, Profile SRP-1, Legal pack UK GDPR, Legal mapping reference `LEGAL-2026-001` (3+ characters).
- First time: **Confirm residency** → "Residency profile updated".
- Later changes: "Ask another administrator to approve" → (second admin approves) → **Apply profile**.
- Negative: recovery cell `us-1` → `BACKUP_REGION_DENIED`.

**Transfer rule:**
- Fields: Data class DC1, Destination `eu-1`, Purpose `FRAUD_REVIEW`, Mechanism `uk_idta`, Legal approval reference, Approval request ID.
- Negative tests:
  - `eu_sccs` under UK GDPR → `RESIDENCY_TRANSFER_DENIED`.
  - Creator approves → "needs an independent approver".

**Check a transfer** → "Transfer allowed" or "Transfer denied: <code>".

## 12. Workload identity: `/admin/workloads`
Needs an agent with an **approved blueprint release** (§19).

**Register binding:**
- Fields: Agent, Namespace `agents`, Environment Production, Proof ATTEST-2, Valid 90 days.
- The SPIFFE ID is built for you: `spiffe://local-1.local.id.zoiko/t/<tenant>/ns/agents/sa/<agent>`.
- Expected: "Workload binding registered", status **Active**.

Negative tests:
- The same SPIFFE ID again → `WORKLOAD_IDENTITY_ALREADY_BOUND`.
- Another tenant's ID in the path → `SPIFFE_ID_INVALID`.
- An unapproved release → `AGENT_BLUEPRINT_NOT_APPROVED`.

**Quarantine environment:**
- `dev` with reason `COMPROMISE_SUSPECTED` → "Environment dev quarantined".
- Again → `ENVIRONMENT_ALREADY_QUARANTINED`.
- Release → "Environment released".

## 13. Security signals: `/admin/signals`
**Create stream:**
- Fields: Receiver; Audience `https://receiver.example.com`; Transmitter issuer `https://idp.example.com`; JWKS above; Events `session.revoked, credential.compromised`; ES256; both subject formats; Trusted.
- Expected:
  - "Stream created — It stays PENDING until you enable it".
  - A one-time push `Bearer …` value and `/ssf/events/push/<tenant>.<stream>`.
- Then Enable / Disable.

**Security state:**
- Look up a subject.
- **Apply override:** new state + justification (3–500 characters) → "Security state set to X (revision N)".

**Publish signal** (testing tool): expect `SCOPE_INSUFFICIENT` "signals:publish is required". The console token lacks that scope.

## 14. Verification: `/admin/verification`
Tabs: Profiles, Adapters, Bulk start, Review & reliance, Principal lookup.

**New profile:**
- Fields: Name `qa-gb-standard`, IAL-Z2, Claims `given_name, family_name, birthdate`, Jurisdictions `GB, IE`, Freshness `365`, Government id, Manual review on, 1 reviewer, Reliance purposes `account_opening`.
- Expected: "…v1 created as a draft". **Activate** → **ACTIVE**.
- Negative tests:
  - `GBR` → "not a two-letter country code".
  - Biometric `FR` outside the jurisdictions → error.
  - Freshness 0 → error.
  - No evidence ticked → "Accept at least one evidence type."

**Register adapter:**
- Fields: ID `qa-idv-1`, Provider `QA IDV`, Mapping `1`, Group `qa-group`, Endpoint `https://api.provider.example/verifications`, Evidence `GOVERNMENT_ID`, Jurisdictions `GB`, JWKS above.
- Expected: "Adapter registered", Circuit **Closed**.
- Negative tests: ID `QA_1` → error. `http://x` → "must be an https:// URL".

**Bulk start:** needs an ACTIVE profile.
- One UUID per line, ≤50.
- `abc` → "Not an ID: abc".
- Expected: "X of Y started".

**Manual review decision:**
- Fields: Case UUID, Approve, reason `DOCUMENT_MATCHES`.
- Expected: "Decision recorded".
- Cannot be used on your own case.

**Reliance decision:**
- Fields: Subject UUID, Purpose `account_opening`, IAL-Z2, Claims `given_name, birthdate`, `GB`.
- Expected: an Outcome badge.

## 15. Credentials & trust: `/admin/credentials`
**New configuration:**
- Fields: Type `https://credentials.example.com/employee`, SD-JWT VC, Token status list, Claims `given_name, family_name`, R1.
- Expected: "…created as a draft". **Activate** (cannot be undone).
- Negative: no claims → "Add at least one required claim."

**Add issuer:**
- Fields: `https://issuer.example.com`, type as above, `GB`, Pinned keys (JWKS), ES256.
- Expected: "…is now trusted".
- Follow-ups: Change status, Rotate keys.

**Register relying party:**
- Fields: an existing OAuth client, Purpose `age_verification`, Attributes `age_over_18, given_name`, Retention 0–3650.

**Create presentation request:**
- Fields: an active configuration, `age_verification`, `GB`, claim `age_over_18`.
- Expected: a wallet link.

**Evidence upload:** PDF/PNG/JPEG only. `.txt` → error. Needs a malware scanner (`ZID_PROOF_SCANNER_ADDRESS`).

**Issued tab:** Suspend / Reinstate / Revoke. Credentials are issued by wallet only; there is no admin button.

## 16. Ownership chains: `/admin/ownership`
1. **New:** keep the starter JSON.
2. **Work out candidates:**
   - person-1 at 30% shows "Needs evidence".
   - person-2 at 30% shows "Needs confirmation".
   - Threshold 25%.
3. **Record version** → "Version 1 recorded".

Negative tests:
- `[]` → "A graph is one JSON object."
- Self-link → `SELF_OWNERSHIP`.
- 150% → `PERCENT_INVALID`.
- A loop → `OWNERSHIP_CYCLE`.

**Confirmation** (by the **second member**):
- Fields: Confirmed, a reason, an evidence reference.
- Expected: "Decision recorded".
- The author sees "someone else decides" (`REVIEWER_IS_AUTHOR`).

## 17. Company verification (grade page): `/admin/company-verification`
This is not the registry claim, which lives at `/admin/organization`. **Both menu items are called "Company verification"**, which is confusing.

**Assess again** → K0–K4 grade.

**Record a mandate:**
- Fields: **another** person's UUID, Role Director, "rests on" Board minute, Document `Board minute 2026-11-01`, Acts (Sign an agreement).
- Expected: "Record the mandate". Payment, transfer or delegate acts show "Needs a second signatory"; a third person signs.
- Negative: your own UUID → "You cannot record your own mandate."

**Appeal:** a reason of 20+ characters.

**Signed summary:** "What is it for?" `Bank onboarding` → Download.

## 18. Identity proofing, Screening, Age check, Trust store, Verification checks
- **Identity proofing cases** (`/admin/idv-cases`) and **Passport check** (`/account/identity-proofing`):
  - Locally: "not available for that country yet" (`config/idv/market-policies.yaml` is empty).
  - With a configured policy, the photo checks are: JPEG/PNG between 20 KB and 6 MB, and 3 attempts.
  - Reviewer rules: two reviewers, and the applicant cannot review.
- **Screening** (`/admin/screening`):
  - `config/screening/market-policies.yaml` is empty, so expect "Not available yet".
  - Form: Market `GB`, Purpose `KYB`, Your company, `Acme Widgets Limited`.
  - Negative tests: `GBR`, birth `30/04/1980` → format errors.
- **Age check** (`/admin/age-check`):
  - At least 18 with `2015-01-01` → "Under the age".
  - `1990-01-01` → "Not decided". A typed date never passes.
  - `1990-13-01`, `2099-01-01`, `01/01/1990` → errors.
- **Chip trust store** (`/admin/trust-store`):
  - Stage (master list + anchor certificate + 64-hex fingerprint + source URL) → Validate → Approve (**a different person**) → Activate.
  - A wrong fingerprint → "That is not this anchor's fingerprint."
- **Verification checks** (`/admin/checks`):
  - **Policy:**
    - Fields: `GB`, `ONBOARDING`, `UTILITY_BILL=90, BANK_STATEMENT=60`.
    - Flow: Save draft → Submit → a second person approves (reason 10+ characters).
    - Negative: `UTILITY_BILL:90` → "Write TYPE=days".
  - **Open case:** reference `cust-0042`. Because `check-adapters.yaml` is empty, expect "Case opened: this step is unavailable".
  - **Email code:** goes to Mailpit.

## 19. Agents & tools: `/admin/agents`
Do the steps in this order. Steps marked ★ need a second person.

1. **Tools → Register tool:**
   - Fields: Name `evidence.lookup`, Server (any app), Action `evidence.read`, Risk 1, Side effects None.
   - Expected: "Tool registered as a draft". Then **Activate**.
   - Negative tests:
     - Name `Evidence*` → error.
     - A duplicate → `TOOL_EXISTS`.
     - External transaction → the "Requires a mandate" box is locked on.
2. **Builds → Register by hand:**
   - Image: `acme/invoice-agent:1.0.0@sha256:<64 hex>`.
   - ★ Approve.
   - Negative tests: self-approve → `AGENT_REVIEW_NOT_INDEPENDENT`. A duplicate digest → `BUILD_EXISTS`.
3. **Blueprints → Draft release:**
   - Fields: Name `finance-approval-bot`, `1.0.0`, the approved build, tool `evidence.lookup`, Publisher.
   - ★ Approve (not the publisher) → "Release approved. It is now immutable."
4. **Agents → Register agent:**
   - Fields: `Acme Invoice Agent`, the approved release, Sponsor/Operator, tool `evidence.lookup`, Anthropic, depth 1.
   - Expected: "Agent registered as a draft".
5. **Workload binding** (§12), or **Register API client**:
   - Fields: Name `Invoice reader`, Audience `https://api.acme.example/invoices`, JWKS.
   - ★ Approve under Builds, then "Bind approved client".
6. **Agent detail:**
   - **Submit for approval**.
   - **Attest** the 4 checks: Security review ★ (not the sponsor, operator or publisher), Kill path, Short-lived credentials, Traceable.
   - **Activate**. Any gate still missing shows in "Still blocking".

## 20. Agent governance: `/admin/agents/governance`
**First, set the baseline through the API (there is no form).** In DevTools, use "Copy as cURL" on any console request and change it to:

```
PUT http://localhost:3000/api/bff/v1/machine/governance/baseline
{"allowed_tools":["evidence.lookup"],"allowed_regions":["local-1"],"ai_providers":[],
 "quotas":{"concurrent_runs_per_agent":1,"runs_per_agent_day":5,"records_per_run":100,"run_seconds":120},
 "schedules_allowed":false}
```

Negative tests:
- `["*"]` regions → `WILDCARD_REFUSED`.
- `records_per_run:1000` → `QUOTA_ABOVE_PLATFORM_CEILING`.
- An unknown tool → `TOOL_NOT_REGISTERED`.

**Signers:**
- Give the Security / Authority / Privacy / Finance slots to **other** people.
- Choosing yourself → "Choose someone else".

**Permissions → Request a change:**
- Fields: Tool, `mandate`, `mandate:`, `access_posture_review`, `local-1`, 30 days.
- Wildcards or empty values → "Use exact values".
- Simulator: the ID must contain `synthetic`. A real ID → `SIMULATION_REAL_DATA_REFUSED`.

**Approvals:** two signers each approve with a passkey within 24 h, then **Make this version live**.

| Negative test | Expected |
|---|---|
| Requester approves | `SEPARATION_OF_DUTIES` |
| Same signer twice | `ALREADY_SIGNED` |
| Request older than 24 h | `REQUEST_EXPIRED` |

**Limits:**
- Set the Company cap first: `5`. The result is "Saved as pending" (Finance + Security must sign).
- Then This agent: `2`.
- Negative tests:
  - An agent cap before a company cap → `NO_PARENT_CEILING`.
  - An agent cap above the company cap → `BUDGET_ABOVE_PARENT`.
  - `5.123` → error.

**Monitoring:**
- **Verify the chain** → "Chain intact".
- **Pause:** a reason plus the typed word `PAUSE`.
- Restore needs two people, not the one who paused (`RESTORE_NOT_INDEPENDENT`).

## 21. Access readiness: `/admin/agents/readiness`
Needs an agent whose blueprint ID is **`daara`** and whose tools are only `read_mandate_metadata`, `read_agent_identity_status` and `create_access_review_proposal`.

**Wizard:**
1. Fields: Display name `Access Readiness — Finance`, Mandate metadata, 100 records, 14 days.
2. Run sandbox test.
3. Request publishing approval.
4. ★ Approve.
5. Start a run.

Negative tests:
- 600 records → `SCOPE_INVALID`.
- A different blueprint → `BLUEPRINT_NOT_ALLOWED`.
- Self-approve → `APPROVAL_NOT_INDEPENDENT`.

**Proposal page** (`/admin/agent-review/{id}`): Dismiss / Assign / Handoff. None of these changes anyone's access.

## 22. Verification workflows: `/admin/workflows`
**New:**
- Change `"id"` to `kyc_basic`. **Check** → "Passes the safety rules". **Save draft**.
- Negative tests:
  - Remove `"else"` → error.
  - An END of `"DECLINED"` → error.
  - A path to approved that skips the checks → error.

**Version page:**
- Simulate.
- ★ Approve. The author sees "someone else approves".
- Retire.

**Start a run (API only):** `POST /api/bff/v2/workflows/kyc_basic/versions/0.1.0/runs {"subject_principal_id":"<test identity>"}`.
- Then record check results and a review.

## 23. Developers: `/admin/developers`
**Webhook:**
- Fields: `https://webhook.site/<id>`, at least one event.
- Steps: **Verify** (the receiver must echo the challenge) → Active.
- Negative tests: `http://…` → error. A private IP → `WEBHOOK_DESTINATION_BLOCKED`.

**Test identities:**
- Fields: prefix `test`, 5, 7 days.
- Expected: "5 test identities created".
- Limits: 20 per batch and 50 live in total (`SANDBOX_LIMIT`).

**Delivery log:** Replay. **Usage** and **Rate limits** are read-only.

## 24. Compliance, Source of funds, Verification spend, Face engine readiness
- **Compliance:** read-only queues.
- **Source of funds:** the product line is in **DESIGN**, so creating a case returns `SERVICE_UNAVAILABLE`. The queue will be empty.
- **Verification spend:**
  - Fields: Limit `2500.50`, `GBP`.
  - Expected: "Monthly limit saved".
  - Negative: `-5`, `1e3`, `GB` → errors.
- **Face engine readiness:** read-only. Check that the page loads.

---

## Defects found while tracing (log these)
1. Policies: an invalid policy name makes "Save draft" do nothing, with no message.
2. Delegation templates: no inline errors (Save just stays disabled). There is also no Apply button, although the API exists.
3. Operator console → Case reviews: the screening ruling accepts a 10-character reason, but the server needs 20.
4. Two menu items are both called "Company verification" (`/admin/organization` and `/admin/company-verification`).
5. Governance review queue links to `/admin/agents/review/{id}` (the retired DAARA page, a 404), while readiness links to `/admin/agent-review/{id}`.
6. Compliance page: workflow-run rows link to the workflow version, not the run.
7. There is no UI to start a workflow run, to set the agent governance baseline, or to create public-verifier disclosure grants.
8. The local Keycloak IdP cannot be connected, because the page requires https on a public DNS name.
