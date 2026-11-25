# Zoiko iD: full frontend guide and test data (every sidebar page)

Traced from the code on 2026-11-24 (branch `akarsh`). Customer console: `http://localhost:3000`.
This guide covers what the product is, how to get a test account, and for **every sidebar page**: what it is for, what you get from it, the fields with valid and invalid test data, and its advantages and disadvantages.

The **Administration** pages are traced field by field in [admin-manual-test-checklist.md](admin-manual-test-checklist.md). Part 7 of this guide gives the purpose, advantage and disadvantage of each of those pages, plus the key test values, and points to the matching checklist section (§N) for the full field tables.

---

## Part 1. What Zoiko iD is

### In one sentence
Zoiko iD is a **trust layer** for companies. It answers the question "**Is this person, company or AI agent really who they say they are, and are they allowed to do this specific thing right now?**" It also keeps signed proof of every answer.

### The problem it solves
A normal login system (Okta, Microsoft Entra, Google) only proves *who you are*. It does not answer:
- Is this company real and registered? Is this person really a director of it? (company verification, KYB)
- Is this person's identity proven with a passport? (identity verification, KYC)
- May this finance clerk release a payment of £40,000 on behalf of the company? Who gave them that right, and is it still valid? (authority and mandates)
- An **AI agent** wants to call a tool or spend money. Who owns it, what may it do, and what is its budget? (agent identity)
- If an auditor or court asks "why was this allowed?", can we prove it? (signed decision evidence)
- A director left the company an hour ago. Is their authority revoked everywhere within seconds? (continuous revocation)

### "Overlay-first"
Customers **keep their existing login system**. Zoiko iD sits on top of it ("overlay") and adds verification, authority, mandates, evidence and revocation. That is why the Onboarding page offers modes:
- **O0 Native**: Zoiko iD is the login (passkeys).
- **O1 Authorization overlay**: your apps ask Zoiko iD "may this person do this?".
- **O2 Assurance overlay**: Zoiko iD adds identity and company verification levels.
- **O3 Federated trust overlay**: trust is shared with other organizations.

### Who uses it
| User | What they do in the console |
|---|---|
| Company Owner / Administrator | Verify the company, invite staff, connect apps and the company's existing login, set grants, mandates, policies, billing |
| Approver | Votes on sensitive changes (four-eyes control) |
| Operator | Runs verification and credential work |
| Viewer | Reads redacted evidence (auditors) |
| Member | Signs in, manages their own passkeys, sessions, privacy and identity check |
| Developers | Register apps, webhooks, test identities; call the decision API |

### Key ideas you will see everywhere
| Term | Meaning |
|---|---|
| **Tenant / workspace** | One customer company's isolated space |
| **Principal** | Anything that can act: a person, an AI agent, a workload (server) |
| **Passkey** | Password-less sign-in with your device (Windows Hello, phone, security key). No passwords or SMS codes are used for sign-in |
| **Step-up** | The site asks for your passkey again before a risky action |
| **Risk class R0–R3** | How dangerous an action is. R3 (critical) needs a sign-in within 15 minutes and is revoked within 2 seconds |
| **Grant** | A written right: "person X may do actions Y on resources Z, up to value V, until date D" |
| **Mandate** | A spending/usage budget taken from a grant, given to a person or an AI agent |
| **Four-eyes / approval** | A second (sometimes third) person must approve. Nobody can approve their own request |
| **Decision receipt** | A signed record of every important allow/deny answer, replayable later for auditors |
| **IAL / AAL** | Identity assurance level (how well identity is proven) / authentication assurance level (how strongly they signed in) |
| **Shadow mode** | Zoiko iD answers next to the old system but does not enforce, so you can compare safely |

### How it is built (for orientation)
- `backend/`: Python FastAPI modular monolith (27–29 modules), PostgreSQL. Runs on port **8000**.
- `frontend/apps/web`: Next.js customer console, port **3000**. `frontend/apps/operator`: Zoiko staff console, port **3100**.
- The browser only talks to port 3000. The Next.js server calls the backend with tokens it keeps server-side.

---

## Part 2. Getting ready to test

### 2.1 Start everything (Windows PowerShell)
```powershell
# once
Copy-Item backend\.env.example backend\.env
docker compose up -d postgres
cd backend
$env:UV_PROJECT_ENVIRONMENT = "$env:LOCALAPPDATA\zoiko-id\venv"
python -m uv sync --all-packages
python -m uv run python ..\tools\scripts\bootstrap_local.py      # prints an /enroll?ticket=... link (valid 23 h)

# terminal 1: backend
python -m uv run python ..\tools\scripts\run_local.py --only cell

# terminal 2: web app
cd ..\frontend; Copy-Item apps\web\.env.example apps\web\.env.local; npm run dev:web
```

**Email catcher (Mailpit).** It is not in `docker-compose.yml`, so start it yourself. Sign-up links, email-change links and one-time codes arrive here:
```powershell
docker run -d --name mailpit -p 1025:1025 -p 8025:8025 -e MP_SMTP_AUTH_ACCEPT_ANY=1 -e MP_SMTP_AUTH_ALLOW_INSECURE=1 axllent/mailpit
```
Open http://localhost:8025. The two `MP_SMTP_AUTH_*` settings matter because the backend always logs in to the mail server. Without them, mail silently fails and the backend logs only "registration email failed".

Optional seeds:
- `seed_local_sanction_lists.py` makes Screening work.
- `bootstrap_local.py --operator` also seeds the operator console.

### 2.2 Rules that save you time
- Always use **`http://localhost:3000`**, never `127.0.0.1`. Passkeys refuse IP addresses.
- **You need 3 people for full testing.** Use 3 browser profiles (for example Chrome profile A, Chrome profile B, Edge). Many steps refuse the person who made the request.

| Person | Role | Used for |
|---|---|---|
| **A** (you) | Owner | Everything you create |
| **B** | Administrator, later also Approver | Second approver, reviewer, "not the author" |
| **C** | Operator or Member | Third signer (A2 requests, T3 commitments), Member view tests |

- **Things that are not enforced locally**, so a refusal you expect may not happen:
  - Role checks are report-only. Test them by checking that buttons are hidden.
  - Plan limits run in shadow mode.
  - Passkey step-up runs in shadow mode.
  - Agent governance runs in shadow mode ("Would be refused").

### 2.3 Path 1 (fastest): the seeded Owner
1. Open the `http://localhost:3000/enroll?ticket=…` link printed by `bootstrap_local.py`.
2. Click **Create passkey** and approve the Windows Hello / phone prompt. Then click **Sign in with your new passkey**.
3. You land on `/account?welcome=1` as **OWNER** of "Local Company" (ACTIVE), with every admin grant.
4. Later sign-ins: `/sign-in` → it shows "Signing in to the default workspace" → **Sign in with a passkey**.
5. A new link if needed: `bootstrap_local.py --ticket-only`.

### 2.4 Path 2: the real sign-up (test this too)
1. Go to `/sign-up`. Fill in the form from §4.1 and click **Continue securely**.
2. Open Mailpit and click the link `http://localhost:3000/sign-up/verify?t=…`. It is valid for 30 minutes and works once.
3. **Create passkey** → **Sign in with your new passkey** → "Your account is ready" → **Open your workspace**.
4. You now have a "Personal workspace" in DRAFT status, and your owner status is PENDING_VERIFICATION. Admin pages say "Not permitted".
5. Make it an admin workspace:
   - Run `python -m uv run python ..\tools\scripts\grant_local_admin.py`. It lists workspaces.
   - Run it again with `--tenant-id <id>`. It should print "administrator standing granted…".
6. Sign out. Go to `/sign-in`, type your email in "Your email address or workspace name", click **Continue**, then **Sign in with a passkey**.

### 2.5 Adding persons B and C
1. As A, go to **Team & invitations** → **Invite person**. Give B the role Administrator and C the role Operator.
2. Copy the link (the platform sends **no email** for invitations). Open it in another browser profile and create a passkey.
3. To make B an **Approver**: **Approvals** → **New request** → "Give Administrator or Approver rights" → Approver. This is an A2 request, so it needs 2 independent votes. If only A and B exist, run the request once C has joined.

### 2.6 Suggested test order
1. Sign-up, sign-in and recovery (Part 4)
2. My account pages (Part 6), so you have 2 passkeys and recovery codes
3. Company → Team (add B, C) → Overview → Alerts
4. Company verification → Organization setup → Domains → Applications → Integrations → Onboarding
5. Approvals → Email contacts → Settings → Billing → Verification checks
6. Administration (Part 7), in the order of the checklist: Identity providers → Provisioning → Authority → Mandates → Policies → Decisions → Evidence → the rest

---

## Part 3. The sidebar at a glance

What you see depends on your role:
- **Member**: Overview, Alerts, My account.
- **Pending owner**: also Onboarding, Company verification, Organization setup.
- **Billing access holder**: also Billing.
- **Approver / Operator / Viewer**: also the pages their role is for.
- **Owner / Administrator**: everything.

A hidden page is only a courtesy. The server still checks every call.

| Group | Pages |
|---|---|
| **Company** (14) | Overview, Alerts & tasks, Onboarding, Company verification, Domains, Applications, Integrations, Team & invitations, Organization setup, Approvals, Billing, Email contacts, Verification checks, Company settings |
| **My account** (9) | My account, Sessions, Passkeys, My limits, Connected apps, Identity verification, Recovery codes, Passport identity check, Privacy |
| **Administration** (28) | Identity providers, Verification, Credentials & trust, Authority & approvals, Mandates, Policies, Access decisions, Security signals, People & principals, Provisioning, Evidence & audit, Privacy & data rights, Operator access, Residency, Agents & tools, Workload identity, Ownership chains, Company verification (grade), Identity proofing cases, Screening, Age check, Chip trust store, Verification spend, Face engine readiness, Compliance cases, Source of funds, Verification workflows, Developers |

---

## Part 4. Public, sign-up and sign-in pages

### 4.1 `/sign-up`: Create your Zoiko iD
**Use:** create an account from only a country, a purpose and an email address. The server decides the region, tenant and role.

| Field | Valid | Invalid → expected message |
|---|---|---|
| What are you creating this for? | **For myself** (or For an organization) | nothing chosen → "Choose For myself or For an organization." |
| Search countries | `king` or `gb` | n/a (filter only) |
| Country or region where you live | **United Kingdom (GB)**. Also open locally: US, IE, DE, FR | none → "Choose the country you live in."; India/Canada → "Not available yet… Nothing has been stored." and the button is disabled |
| Email address | `tester1@example.com` | `a@b`, `user@example.c`, `john doe@example.com`, `a@b@c.com` → "Enter a valid email address." |

- **Expected:** "Check your email". The same message shows for any address, so it never reveals whether an account exists.
- **Rate limits:** 10 tries per 10 minutes per network and 5 per address per hour → "Too many requests from this network…".

### 4.2 `/sign-up/verify` and `/sign-up/status`
- Valid link → "Email confirmed" with the badges "Account active" and "Identity not verified" → Create passkey.
- The same link opened twice → "This link can't be used".
- `/sign-up/verify?t=short` → idle page ("Verify your email").
- Status steps you may see: Check your inbox / Set up your passkey / Your account is ready / You have an invitation / Registration on hold.

### 4.3 `/enroll`: set up a passkey
- No ticket → "Enrollment link needed".
- Cancel the browser prompt → "The passkey prompt was dismissed or timed out."
- Same security key twice → "This passkey is already registered on this account."
- Opened on 127.0.0.1 → "Passkeys are not allowed on this address."

### 4.4 `/sign-in`
| Field | Valid | Invalid |
|---|---|---|
| Your email address or workspace name | `tester1@example.com` or `local-company` | `nobody@example.com` → "We couldn't find an account for that address…"; `nope-co` → "No company uses that workspace name."; `Bad_Name!` → "Enter your email address or your workspace name."; more than 10 tries in 10 minutes → "Too many tries…" |

- Then **Sign in with a passkey**.
- Try `/sign-in?error=access_denied` → "Sign-in was cancelled or denied."
- Try `/sign-in?reason=expired` → "Your session ended".

### 4.5 `/recover`: lost passkey
- **Email card:** "Email me a sign-in link". **Locally no mail is sent**, because template ZID-EM-010 is not enabled in `ZID_EMAIL_ENABLED_TEMPLATES`. Add it to test this.
- **Recovery code card:** paste a code from My account → Recovery codes.
  - `ABCDE` → "Enter the full recovery code…".
  - A used code → "That recovery code isn't valid, or it has already been used…".
- An Owner/Admin recovering with a code goes to "Verify your identity" / "Waiting for approval", not straight to a new passkey. That is by design.
- **Continue a recovery:** Recovery ID + Recovery key (16+ characters).

### 4.6 Other public pages
- **`/pricing`:** plans, calculator and FAQ, read from the backend catalog.
- **`/services`:** "Practical uses of Zoiko iD".
- **`/verify/<tenant>.<check>?grant=<uuid>`:** public credential check, no account needed. `/verify/abc` → "This link cannot be used".
- **`/consent`:** the "Allow access?" screen for third-party apps. Opened without a request → "Nothing to approve". The first-party `zoiko-web` client skips it.

---

## Part 5. Company group

### 5.1 Overview (`/overview`)
**Use:** your home page. It shows where the company is in onboarding and what to do next.

**What you get:**
- **Admins:**
  - Stage and blocking gates.
  - The lifecycle stepper: Registered → Company verification → Configuring → Integrating → Shadow → Pilot → Live.
  - "Action required".
  - "Waiting for your approval", with **Approve / Decline**.
  - The first 6 team members and **Invite people**.
- **Members:** tiles for their own account.
- **Pending owner:** a **Start verification** card.
- **Anyone with only 1 passkey and no recovery codes:** a "Protect your access" warning.

**Test:**
- Sign in with only one passkey → the warning shows → follow "Save recovery codes" → the warning disappears.
- As B, ask for an approval. As A, approve it here. Expect the toast "Approved".

**Fields:** none.

**Advantage:** the next problem comes first, and every item links to its fix.
**Disadvantage:** after the INTEGRATING stage, "Blocking gates" always shows 0 even when shadow or pilot gates fail. Use Onboarding → **Validate** for the real answer.

### 5.2 Alerts & tasks (`/alerts`)
**Use:** one inbox of platform alerts, most severe first. Examples: a quarantined connector, an expiring credential, a stale domain, a revoked authority.

**Filters:**
- Severity: All / Critical / High / Medium / Info.
- State: Open and acknowledged (default) / Open / Acknowledged / Resolved.

**Test:**
1. Create an alert: Integrations → a connector → **Quarantine**. The HIGH task "An integration connector was quarantined" appears, and the sidebar badge counts it.
2. **Acknowledge** → "Alert acknowledged. It stays open until it is resolved."
3. **Resolve** → "Alert resolved".
4. A critical alert also shows a red banner on every page.

**Advantage:** repeats of the same alert are grouped with a count, and Critical/High alerts cannot be muted.
**Disadvantage:** plain Members see an Alerts tile but probably get "Not permitted" (only Owner/Admin hold `notification.alert.read`). Pressing Acknowledge shows a spinner on every row.

### 5.3 Onboarding (`/admin/onboarding`)
**Use:** a guided wizard from "verified company" to "live". Zoiko iD first watches your apps' access questions without enforcing (**shadow**), then enforces for a small group (**pilot**), then everywhere (**launch**). "There is no launch-anyway bypass."

**Steps:**
1. Organization
2. Domains & residency
3. Identity providers (optional)
4. Provisioning (optional)
5. Applications
6. Policy & plan
7. Shadow
8. Pilot
9. Launch

**Forms and test data:**
| Form | Field | Valid | Invalid |
|---|---|---|---|
| Create plan (step 6) | How will your company use Zoiko iD? | **Authorization overlay (O1)** | n/a (radio) |
| Plan a stage (step 8, PILOT only) | Name | `Finance team, low risk` | blank → button disabled; >120 characters → server error |
| | Highest risk enforced | R1 — low risk | — |
| | Applications | none = all | — |
| Roll back (SHADOW/PILOT) | Why | Owner decision | — |

**Test flow:**
1. **Create plan** → "Onboarding plan created".
2. **Approve plan**: B approves. If A tries, the result is `APPROVER_IS_AUTHOR`.
3. **Start integrating**. It needs a verified domain (5.5), residency set (Administration → Residency), and the Owner.
4. **Start shadow**. It needs an ACTIVE application (5.6).
5. The shadow meters need 100 real decision calls from your app to `/access/v1/evaluation`.
6. **Start pilot** fails with `SHADOW_EXIT_CRITERIA_FAILED` until shadow is ready.
7. **Hidden rule:** before Launch you must roll back once during PILOT and start the pilot again (`pilot.rollback_tested`).

**Advantage:** a safe, staged rollout. Shadow changes nothing for users, rollback is one click, and criteria can only be made stricter.
**Disadvantage:** heavy to complete. It needs a really integrated app, 100+ calls and approvals at every step. The Launch button can show even though the server will refuse it.

### 5.4 Company verification (`/admin/organization` and the claim pages)
**Use:** turn "a company I typed in" into a **verified organization** with you as owner. The company is looked up in an official register, and your own identity is checked too. A search match alone never makes you owner.

**What you get:** status "Company verified", the organization ID, and the gates. This is the foundation for every authority grant.

**Step 1: `/admin/organization`**
- Two cards: **Verify my identity** (goes to `/account/verification`) and **Find my organization**.

**Step 2: Find your organization (`/admin/organization/claim`)**
| Field | Valid | Invalid |
|---|---|---|
| Country of incorporation | GB (also US, IE, DE, FR) | — |
| Registration number or legal name | `Tesco PLC` or `00445790` | `A` → Search stays disabled |

- Local lookups go to **GLEIF only**, so only companies with an LEI are found.
- No match → "We couldn't confirm a matching record".
- **Select** a result → **Continue with the selected company** opens a provisional claim.

**Step 3: Authority (`…/claim/<id>/authority`)**
| Field | Valid | Invalid → message |
|---|---|---|
| Your full legal name | `Jane Mary Smith` | `J` → Submit disabled |
| Your relationship to the company | I am a director or officer | "I work for the company…" with no document → "Choose the document that gives you authority…" |
| Authority document / end date (non-officers) | a Letter of authority uploaded first; date in the future | past date → "The end date must be in the future." |
| Owner: Full legal name | `John Smith` | blank → "Enter the person's full legal name." |
| Birth year | `1970` | `70` → "Enter a four-digit birth year." |
| Share of the company % | `60` | `150` → "Shares must be between 0 and 100." |
| Share of the votes % | `60` | `101` → "Votes must be between 0 and 100." |
| Can appoint or remove most of the board | optional | nothing filled for an owner → "State the shares, the votes or the board control." |

**Step 4: Status (`…/claim/<id>/status`)**
- Two check rows: "Company and legal status" and "Your identity".
- Buttons: **Send for review**, **Check again**, **Withdraw this claim**.
- **Supporting documents:**
  - Type: Certificate of incorporation (default), Register extract, Letter of authority, Ownership chart, Board resolution, Signing matrix, Another document.
  - File: PDF/PNG/JPEG up to 10 MB.
  - Valid: a clear JPEG of a certificate.
  - Invalid: a 12 MB file → "That file is larger than 10 MB."; `.docx` → "Upload a PDF, PNG or JPEG file."; empty file → "That file is empty."
  - PDFs are stored but not read by OCR.
- **Appeal** (only when rejected or in review): a reason of 20–2000 characters. `Too short` → "Write at least a sentence (20 characters)."
- **Local shortcut:** `ZID_ORG_CLAIM_DEV_AUTOVERIFY` makes a claim VERIFIED as soon as your identity check completes. Then "<name> is established" → **Continue to organization setup**.

**Advantage:** honest results: each check is shown separately and a match is never called "verified". A matching certificate photo can complete the company check without a reviewer.
**Disadvantage:**
- GLEIF-only lookup locally.
- The authority/owners page no longer decides anything (only company + identity do), but the UI still presents it as a step.
- There is no list of your existing claims, so keep the claim URL.
- Two sidebar items are both called "Company verification" (see 7.18).

### 5.5 Domains (`/admin/domains`)
**Use:** prove the company controls a domain (for example `acme.com`). People signing in with that email domain are routed to your workspace. At least one verified domain is **required** to start integrating.

| Field | Valid | Invalid → message |
|---|---|---|
| Domain | a domain you control, e.g. `acme-example.co.uk` | `gmail.com` → "Public e-mail domains cannot be claimed."; `localhost` → "Enter a domain such as example.com."; `10.0.0.1` → "IP addresses are not domains."; one already verified by another workspace → `DOMAIN_ALREADY_CLAIMED` |
| How to prove control | DNS TXT record (default) or HTTPS file | — |

- **DNS:** add a TXT record named `_zoiko-challenge.<domain>` with the value `zoiko-domain-verification=<token>`. Then click **Verify** → "<domain> verified".
- **HTTPS:** publish the file `https://<domain>/.well-known/zoiko-domain-verification`.
- Verify too early → "The challenge is not published (yet) or does not match."
- The challenge expires after 7 days. Re-checks run every 30 days. If the record is lost the domain becomes STALE and a HIGH alert is raised.
- **Remove** works immediately, with **no confirmation**.

**Advantage:** strong, re-checked proof. A domain never silently moves to another company.
**Disadvantage:** needs a real public DNS or web server change, so it cannot be fully tested offline. Remove has no confirmation, and "Prove again" does not pre-fill the domain.

### 5.6 Applications (`/admin/applications`)
**Use:** register your company's own apps. Either people sign in to them with Zoiko iD (OIDC), or they ask Zoiko iD "may this person do this?" (AuthZEN). An ACTIVE app is needed to start shadow.

**Form A: Add application** (4 steps)
| Field | Valid | Invalid |
|---|---|---|
| What is your application called? | `Payroll` | `P` → Continue disabled |
| What does it do with Zoiko iD? | It asks Zoiko iD whether someone may do something | — |
| Where does it run? | Production | — |
| Key | **Make a key for me** → then **Download private key** (save it!) | pasted text that is not JSON → generic error; a key containing `"d"` → `JWKS_PRIVATE_KEY`; a key with no `kid` → "each key needs a kid" |
| What will it send and enforce? | Keep it simple (recommended) | — |

- **Expected:** "Application registered" → **Start testing and activate** → "Your application is active".
- Row buttons: Start testing → Activate → Suspend → Resume.

**Form B: Create client** (OAuth client)
| Field | Valid | Invalid |
|---|---|---|
| Application name | `Payroll web app` | `P` |
| What kind of application is it? | Browser or mobile app | Server application with no keys → error |
| Redirect URLs | `https://app.example.com/callback` (http is allowed only for `http://127.0.0.1:…`) | `http://localhost:3000/cb` → "use https…"; `https://*.example.com/cb` → "no wildcard…" |
| What may it ask for? | Sign people in + Read verification results | — |

- **Expected:** a "Your client ID" dialog showing an ID like `payroll-web-app-3f9a21c4`.
- Then **Retire** removes the client (no confirmation).

**Advantage:** no shared secrets. Browser apps use PKCE, server apps use private-key JWT, and the key is made in your browser so the private half never leaves it.
**Disadvantage:**
- Application names over about 61 characters fail.
- Switching "Let me choose" back to "Keep it simple" still sends your custom choices.
- There is no Retire button for applications.

### 5.7 Integrations (`/admin/integrations`)
**Use:** register the systems you already run (Okta, a SCIM/HR directory, webhooks, security signals) as **connectors**. It tracks their health, stores their secrets write-only, maps their attributes onto profiles, and routes logins to the right identity provider.

**Tabs:** Connectors, Mappings, Sign-in routing.

| Form | Field | Valid | Invalid |
|---|---|---|---|
| Register connector | Type | SCIM directory | — |
| | Name | `Okta workforce` | blank |
| | Source | `provisioning_source:<id from Provisioning>` | `okta` → "Must start with provisioning_source:" |
| | What it provides | Provisioning | none ticked |
| | How Zoiko iD authenticates | OAuth client | — |
| | Freshness (minutes) | `60` | `0` or `20000` → "1 to 10080 minutes." |
| Credential | Secret | `s3cr3t-Value-Example-1234` | `short` → button disabled (16+ characters) |
| | Expires | a future date | a past date → "not_after is in the past." |
| Quarantine | Reason | Suspected compromise | — |
| Mapping release | Rules (JSON) | keep the pre-filled example → **Simulate** → **Activate** (B approves) | `[{"source":"claims.dept","target":"authority.signer"}]` → `MAPPING_TARGET_FORBIDDEN` |
| Add a route | When / Match / IdP | E-mail domain / `example.com` / `federation_connection:<id>` | `abc1234567` → "Routes point at a federation connection." |
| Test routing | E-mail domain | `example.com` → "Signs in with …" | unknown → "No route: signs in with a passkey." |

- Quarantine takes effect at once and raises an alert.
- **Release** from quarantine needs a second admin.
- Storing a credential on a quarantined connector → `CONNECTOR_QUARANTINED`.

**Advantage:** safe by design. Secrets are write-only, quarantine is instant, release needs two people, and mappings must pass simulation and can never grant signing authority.
**Disadvantage:** mapping releases and login routes look configurable but do **not yet appear to be used** by real sign-in or provisioning; only the test "Resolve" uses them. Rules are raw JSON, which assumes technical users.

### 5.8 Team & invitations (`/admin/team`)
**Use:** invite colleagues. Each one gets a one-time link and creates **their own** passkey. You can also see roles, the org structure, custom roles and delegations.

**Tabs:** People, Roles and permissions, Structure, Custom roles, Delegations.

| Form | Field | Valid | Invalid |
|---|---|---|---|
| Invite person | Name (optional) | `Priya Nair` | 121+ characters → `NAME_INVALID` |
| | Role | Member / Operator / Viewer / Administrator | Owner and Approver cannot be picked (use an Approval request) |
| Structure → Add unit | Name | `Acme UK` | `A` → disabled; a duplicate → `ORG_UNIT_EXISTS`; a 7th level → `ORG_UNIT_TOO_DEEP` |
| | Kind / Under | Subsidiary / Top level | — |
| Custom roles → New | Name | `Profile reader` | `P` → disabled |
| | What it does / Built on | Narrow / Operator, tick 1 action | — |
| Delegations → New template | Name | `Supplier payments up to 5,000` | `A` → disabled (no message) |
| | Actions / Resources | `payment.release` / `payment:*` | blank |
| | Value / Currency / Days | `5000` / `GBP` / `30` | days `0` or `400`, currency `GB` → disabled (no message) |

- **Invite flow:** "Shown once" link → tick "I have shared the link…" → **Done**. Progress goes Link shared → Passkey created → Signed in → Joined (refreshes every 5 s).
- Row buttons: **New link** (only before a passkey exists), **Name**, **Remove** ("Access removed", no undo).

**Advantage:** passkeys are never shared, and you can watch the invitee's progress live.
**Disadvantage:** **no invitation email** is sent; you copy the link yourself. Template and unit forms give no error text (the button just stays disabled). The delegation register shows 8-character IDs instead of names.

### 5.9 Organization setup (`/organizations/current/setup`)
**Use:** a checklist of what is verified and what setup remains. **Activate organization** works only when the server says every condition holds. "Nothing on this page can be bought or skipped."

**Sections:**
- **What has been verified:** Legal entity, Your identity.
- **Required setup:** Accept organizational controls; Secure every privileged account (each Owner/Admin needs a passkey); Accept data region and policy.
- **Independent approvers:** recommended.
- **Activation.**

**Test:**
1. Click both **Accept** buttons → "Recorded".
2. **Activate organization** → "Organization activated". If anything is missing → "Refused by the server: …" with "Fix this" links.
3. Invite an Admin who has no passkey → the "Secure every privileged account" item fails.

**Fields:** none (buttons only).

**Advantage:** the checklist is computed on the server, so it cannot be faked.
**Disadvantage:** it cannot finish the identity or company checks itself, only link to them. A new version of the control texts puts an active organization back to "Active, with limits" until someone re-accepts.

### 5.10 Approvals (`/organizations/current/approvals`)
**Use:** sensitive (A1) and critical (A2) company changes are made **by request**, approved by independent people with their passkeys, and then applied. This is the four-eyes control.

**Tabs:** To review, Submitted by me, History. Filters: Level, State, Created since, Request id.

**New request:**
| "What do you want to change?" | Level | Extra field: valid value |
|---|---|---|
| Invite a team member | A1 (1 approver) | Which role: Viewer |
| Give Administrator or Approver rights | A2 (2 approvers) | Which role: Approver; pick person B |
| Add an Owner / Remove an Owner | A2 | the person |
| Recover a privileged account | A2 | person + `IDCHECK-2026-001` |
| Reinstate a suspended organization | A2 | Reason: `Incident closed` (max 64) |
| Activate a powerful agent | A2 | agent id |
| Ask Zoiko iD to review a trust decision | A2 | reference + why (max 500) |
| **Why are you asking?** (always) | | `New auditor joining the finance team for Q4 review` |

- Invalid: an empty Why → "Say why you are asking."; a 70-character reinstate reason → "…is longer than 64 characters."

**Detail page:**
- **Evidence upload** (requester, before the first vote): PDF/PNG/JPEG up to 10 MB.
- **Approve** (passkey), **Reject** (a reason is required; blank → "Give a reason."), **Request changes** (voids the existing votes), **Apply this change**, **Revise and resubmit** (JSON; bad JSON → "This is not a valid JSON object."), **Withdraw**.

**Test:**
1. A creates a request; A sees "Not eligible" on their own request.
2. B approves (and C too for A2).
3. A clicks **Apply** → Executed.
4. Approving from an old tab → "This request changed" → **Refresh**.

**Advantage:** real four-eyes control. Each vote is bound by passkey to the exact request, and a changed request voids earlier votes.
**Disadvantage:** a one-person company can never complete A1/A2 requests (by design, never waived). Revising a request means editing raw JSON.

### 5.11 Billing (`/admin/billing`)
**Use:** plan, usage, estimated bill, invoices, card payments (Stripe), plan-change requests, solution quotes, budgets, and who may see billing or sign commitments.

**Tabs:** Overview, Subscription, Plans, Plan features, Configure a solution, Usage, Bill preview, Invoices, Commitments, Billing profile, Payment, Budgets, Billing access.

| Tab / form | Field | Valid | Invalid |
|---|---|---|---|
| Plans → Ask for this plan | What do you need it for? | `Two more SSO connections for our EU subsidiary` | >1000 characters |
| | "I understand this only asks…" checkbox | ticked | unticked → Send disabled |
| Configure a solution | Purpose | Customer onboarding | — |
| | Country of the person / payer | `DE` / `DE` | `Germany` → "Use a two-letter country code, for example DE." |
| | Data region | `eu-central` | `e` → "Use 2 to 40 characters." |
| | Expected checks per year | `12000` | `1.5` → "Enter a whole number, or leave blank." |
| Commitments → Who may approve | Person / Authority / Limit (USD) / Until | B / Billing Admin spending limit / `25000.00` / `2027-06-30` | `0`, `-5`, `25000.123` → Give limit disabled |
| Billing profile | Address line 1 / City / Postal / Country | `1 King Street` / `London` / `EC2V 8AU` / `GB` | blank line 1 → "Enter the first line of the address."; `G` → "Use the two-letter country code, for example GB." |
| | Tax identifier | VAT `GB123456789` | `12` → "Each tax identifier is 4 to 40…"; two VAT rows → server `TAX_ID_INVALID` |
| | Invoice email | `ap@acme.co.uk` | `ap@acme` → "That does not look like an email address." |
| Budgets → Set a budget | Budget for / Monthly limit | The whole monthly bill / `1500` | `0`, `abc` → "Enter a positive number." |
| Billing access → Give | Person / Access / Why / Until | B / Billing Viewer / `Finance month-end close` / `2027-01-31` | yourself → `BILLING_SELF_GRANT`; a past date → `BILLING_EXPIRY_PAST`; a non-Owner → `BILLING_DELEGATION_OWNER_ONLY` |

- **Payment:** "Add a card" or "Pay on Stripe" open Stripe's hosted page.
- Locally, without Stripe keys: "Card payments are not switched on yet".
- Without an approved price book: "Not priced yet".

**Advantage:** figures come from the ledger, card data stays on Stripe, and billing powers need a passkey and can expire.
**Disadvantage:** no self-service checkout (ask the team, sign an order, pay). Many tabs are empty until Zoiko approves a price book and links the billing account.

### 5.12 Email contacts (`/admin/contacts`)
**Use:** tell Zoiko iD where to send billing, security, owner and technical emails. Each person can also change where their own security notices go.

| Form | Field | Valid | Invalid |
|---|---|---|---|
| Add a contact | Purpose | Billing (also Security, Owner, Technical) | — |
| | Email address | `billing@acme-example.com` | `billing@acme`, `"Bob" <bob@acme.com>` → "Enter a plain email address, such as billing@yourcompany.com." |
| My security notices | My email address | `me.new@example.com` | `me@example` → "Enter a plain email address." |

- Saving your own address → "Check the new inbox…". In Mailpit, open the link to `/account/email-change?t=…` → **Confirm the new address** → "Your security email was changed". The old address is notified.
- **Remove** has no confirmation.

**Advantage:** addresses are stored encrypted and shown masked. A change only happens after the new inbox confirms, and the old inbox is always told.
**Disadvantage:** if email is not configured, the page still says "check the new inbox" but nothing ever arrives.

### 5.13 Verification checks (`/admin/checks`)
**Use:** run individual checks on a customer: email/phone code, address, proof-of-address document, postal code, device. A company policy is set per country and purpose. Each result says what it **proves and does not prove**.

| Form | Field | Valid | Invalid |
|---|---|---|---|
| Verification policy | Country (ISO) | `GB` | `GBR` |
| | Purpose | `ONBOARDING` (capitals!) | `onboarding` → server 422 (the UI does not uppercase it) |
| | Accepted documents | `UTILITY_BILL=90, BANK_STATEMENT=60` | `UTILITY_BILL:90` → "Write TYPE=days…"; `UTILITY_BILL=120` → `DOCUMENT_AGE_ABOVE_PLATFORM`; country `FR` → `DOCUMENT_TYPE_NOT_ACCEPTED_BY_PLATFORM` |
| | Reason (approve/reject) | `Approved for UK onboarding pilot` (10+ characters, **B** approves) | author approves → `APPROVER_IS_AUTHOR` |
| Open a case | Your reference for the person | `cust-0042` | `ab`, `jane@acme.com` → 422 |
| | Country / Purpose | `GB` / `ONBOARDING` | — |

- **Flow:** Save draft → Submit → B approves.
- Opening a case shows "Case opened: this step is unavailable".

**Advantage:** honest results, and policies need a second approver.
**Disadvantage:** locally **no check adapters are configured** (`check-adapters.yaml` is empty), so the email/phone/address cards never appear. Only policy drafting/approval and empty cases can be tested.

### 5.14 Company settings (`/admin/settings`)
**Use:** the workspace control panel.

**Tabs:**
- **Details:** read-only.
- **Owners** and **Lifecycle:** Owner only.
- **Authentication, Notifications, Branding, Enterprise services, Cryptography, History:** Owner or Admin.

| Tab | Field | Valid | Invalid |
|---|---|---|---|
| Owners | Make owner / Remove owner | make B an owner (second admin approves + passkey) | removing the last owner → disabled: "The last owner cannot be removed…" |
| Lifecycle | Suspend → Reason | **Careful:** it runs immediately and signs everyone out. Resume needs a second admin | — |
| Authentication | Maximum session length / Idle timeout | 8 hours / 15 minutes | — |
| Notifications | Routine alerts checkboxes | untick one → Save | muting a mandatory alert → `NOTIFICATION_MANDATORY` |
| Branding | Display name | `Acme Ltd` | `Zoiko Support` → `BRANDING_SPOOFING` |
| | Brand colour | `#0b5394` | `#ffff00` → "too light for white text"; `1a73e8` → "Use a colour like #1a73e8." |
| | Welcome text | `Sign in with your Acme passkey` | >160 characters or containing `<` |
| | Logo | PNG/JPEG/WebP ≤ 48 KB | a 60 KB file → "The logo must be at most 48 KB." |
| | Support link | `https://help.acme-example.com` | `http://help.acme.com` → "must be an https:// URL" |
| Enterprise services | Service / What you need / Who should we contact | Custom SLA / `24x7 P1 response within 30 minutes, named TAM` / `security@acme-example.com` | `SLA` (<10 characters) or `ab` → Send disabled |

- Editing in two tabs at once → "This setting changed while you were editing" → **Reload current value**.

**Advantage:** every change is versioned, and security floors can only be tightened (shorter sessions, mandatory alerts, no impersonating Zoiko).
**Disadvantage:** Suspend has no confirmation. Notifications are in-app only. BYOK/HYOK are display-only. Enterprise services are manual requests.

---

## Part 6. My account group

### 6.1 My account (`/account`)
**Use:** your personal dashboard.

**What you get:**
- Active sessions, Passkeys, Connected apps, Security state.
- Your Principal ID and Tenant ID.
- A banner for any pending invitation ("Join").

**Fields:** none.

**Advantage:** each card loads on its own, so one failure never shows a false zero.
**Disadvantage:** shows raw IDs, not your name or email.

### 6.2 Sessions (`/account/sessions`)
**Use:** see everywhere you are signed in and end any session.

**Test:**
1. Sign in from a second browser.
2. **Sign out** on that row → "Session signed out".
3. **Sign out everywhere** → "N sessions signed out", and you are logged out too.

**Advantage:** ending a session also kills the tokens it issued.
**Disadvantage:** no device, browser or IP is shown, so sessions are hard to tell apart.

### 6.3 Passkeys (`/account/passkeys`)
**Use:** add a backup passkey (phone or security key) or remove one.

| Field | Valid | Invalid |
|---|---|---|
| Name (optional) | `Work laptop` | 65+ characters → server error; the same key again → "This passkey is already registered on this account." |

- **Add a passkey** → "Passkey added".
- **Remove** → "Passkey removed". Every session ends.
- Remove is disabled on your only passkey ("Your only passkey").

**Advantage:** you cannot lock yourself out.
**Disadvantage:** the 64-character name limit is only checked by the server.

### 6.4 My limits (`/account/limits`)
**Use:** shows the spending/action **mandates** you hold or set up for others or agents, and how much is left.

**Test:**
1. First create a mandate in Administration → Mandates, with you as executor.
2. The card shows Limit, Each payment, Valid until and the balance.
3. **Pause it** / **Cancel it** → "Mandate is now Paused" / "Revoked".

**Fields:** none.

**Advantage:** only buttons the server allows are shown.
**Disadvantage:** nothing can be created here. A new account sees only "You have no limits set".

### 6.5 Connected apps (`/account/connected-apps`)
**Use:** third-party apps you allowed through the "Allow access?" screen. Remove their access here.

**Test:** needs a non-first-party OAuth client that went through `/consent` (create one in Applications). Then **Remove access** → "Access removed".

**Advantage:** permissions are explained in plain words.
**Disadvantage:** the app is shown by its raw client ID, and access tokens it already holds keep working until they expire. Empty by default locally.

### 6.6 Identity verification (`/account/verification`)
**Use:** prove your own identity so you reach a higher assurance level (needed, for example, to claim a company). It shows your level, your risk tier and what the next tier needs.

**Local test (simulator):**
1. With no active profile, click **Prepare development checks** (Owner/Admin). This creates a GB IAL-Z2 profile.
2. Fill in:

| Field | Valid | Invalid |
|---|---|---|
| Verification check | Representative proofing (v1) | — |
| Country of your documents | GB | a closed country → "Identity verification isn't open for this country yet" |
| Purpose | `representative_proofing` (or `account_verification`) | blank → Start disabled |

3. **Start verification** → "Verification started".
4. The dev provider panel appears:
   - Legal name: `Jane Mary Doe`.
   - Date of birth: `1985-06-15`.
   - Provider result: "Document and liveness pass" → **Complete check** → "Provider answered: verified".
5. Also try "Check fails" and "Fraud signal" (a fraud signal never auto-passes).
6. Tier card: tick "I can't use a camera" → alternatives appear (Assisted proofing, eID).

**Advantage:** the whole flow, including signed provider callbacks, can be tested locally.
**Disadvantage:** the simulator covers GB only and uses typed values, not a real document capture.

**Related page:** `/account/verification/session/<ref>` (privacy notice and biometric consent before the camera). Nothing in the UI links to it; you need a session ref from the API.

### 6.7 Recovery codes (`/account/recovery`)
**Use:** one-time codes to get back in if you lose all passkeys. You can also stop a recovery you did not start.

**Test:**
1. **Generate new codes** (passkey) → "Shown once" → **Copy all** / **Download .txt** → tick "I have saved these codes…" → **Done**.
2. Use one code on `/recover`.
3. **Stop this recovery:** Recovery attempt ID (from `/recover`) → "That recovery attempt was stopped…".

**Advantage:** codes are never stored in the browser.
**Disadvantage:** an Owner or Admin still needs re-proofing and approval, so a code alone will not restore a privileged account.

### 6.8 Passport identity check (`/account/identity-proofing`)
**Use:** verify yourself with a passport (or Aadhaar). Upload the photo page, take a live selfie, and the faces are compared. The result is Verified, Rejected or Retake.

| Field | Valid | Invalid |
|---|---|---|
| Document | Passport | — |
| Country that issued the passport | `GB` | `G`, `GBR`, `1A` → Continue disabled |
| Document photograph | JPEG/PNG, 20 KB – 6 MB | `.pdf` → "Use a JPEG or PNG photograph."; 8 MB → "…Use one under 6 MB."; 10 KB → "That photograph is too small to read." |
| Live photograph | a webcam or a file | — |
| Agreement checkbox | ticked | unticked → Send disabled |

- **Expected:** "Attempt n of 3" and "Face comparison: similarity x, needed y".
- **Locally it will refuse:** "Identity proofing is not available for that country yet", because `config/idv/market-policies.yaml` is empty. To enable it you need a market policy, `ZID_IDV_FACE_ROUTE_ACK`, the OCR sidecar and the face sidecar.
- **The passport chip (NFC) cannot be tested.** It needs the phone app, which is not in this repository.

**Advantage:** shows the real similarity score, and retakes are not held against the person.
**Disadvantage:** no liveness or document-genuineness check on this route. **Likely bug:** passport cases show "photograph the front of your Aadhaar card".

### 6.9 Privacy (`/account/privacy`)
**Use:** your data rights: opt-outs, consents, and requests to get, export, correct or delete your data.

| Section | Field | Valid |
|---|---|---|
| Privacy choices | 4 switches (Do not sell/share, Limit sensitive data, No direct marketing, Opt out of automated decisions) | toggle → "Preference saved" |
| Optional uses | "I have read notice v… and I agree." | tick → **Give consent**; then **Withdraw** |
| New request | What would you like to do? | Get a copy of my data |
| | Applicable law | `uk-gdpr` |

- **Submit request** → "Request received". Status: "Received. We confirm it is you before we act."
- Choosing "Delete my data" shows the warning "Deletion cannot be undone".
- Downloads (`my-zoiko-data.json`) need a fresh sign-in and expire after 7 days.

**Advantage:** opt-outs apply at once, with no documents needed.
**Disadvantage:** requests stay RECEIVED until an admin processes them in Administration → Privacy & data rights. "Correct my data" has no field to say what to correct.

### 6.10 Email change confirmation (`/account/email-change`, not in the sidebar)
Opened from the email sent by Email contacts → My security notices (see 5.12).
- A bad or old link → "The link is not valid. Ask for a new one." Limits: 5 tries, 30 minutes, single use.

---

## Part 7. Administration group

Full field tables with valid samples and negative tests: **[admin-manual-test-checklist.md](admin-manual-test-checklist.md)**. Below: what each page is for, what you get, advantages and disadvantages, and the key test data.

### 7.1 Identity providers (checklist §1)
**Use:** connect the company's **existing login** (Entra ID, Okta, Google Workspace, any OIDC, or SAML 2.0) so staff sign in with their corporate account. This is the heart of "overlay-first". Trust anchors let you trust a federation of providers.

**Key test data:**
- Name `Sign in with Acme`.
- Issuer `https://login.acme-test.com`.
- Client ID `zoiko-id`.
- JWKS from checklist §0.
- Algorithms ES256 and RS256.
- Negatives: `http://` issuer, a duplicate issuer, a JWKS with `"d"`.

**Advantage:** customers keep their IdP. Changes are revisioned, keys can be rotated, and a provider can be quarantined in one click.
**Disadvantage:** needs https on public DNS, so the bundled local Keycloak cannot be connected. Real sign-in through a fake provider fails.

### 7.2 Verification (§14)
**Use:** define identity-verification **profiles** (required level, claims, countries, evidence, manual review). Connect **adapters** to IDV vendors, start checks in bulk, review cases, and make **reliance** decisions (can we rely on this person's verification for purpose X?).

**Key test data:**
- Profile `qa-gb-standard`, IAL-Z2, claims `given_name, family_name, birthdate`, `GB, IE`, freshness 365.
- Adapter `qa-idv-1`, `https://api.provider.example/verifications`.

**Advantage:** vendor-neutral and reusable; one profile serves many apps.
**Disadvantage:** real checks need vendor adapters. Locally only the simulator works.

### 7.3 Credentials & trust (§15)
**Use:** digital credentials (SD-JWT VC). Define credential types, trust outside issuers, register relying parties, and create presentation requests (for example "prove you are over 18" without showing a birth date).

**Key test data:**
- Type `https://credentials.example.com/employee`.
- Issuer `https://issuer.example.com`.
- Purpose `age_verification`, claim `age_over_18`.

**Advantage:** privacy-preserving selective disclosure that is standards-based (OID4VC).
**Disadvantage:** credentials are issued by wallet only (no admin button). Evidence upload needs a malware scanner.

### 7.4 Authority & approvals (§3)
**Use:** **grants** say exactly who may do what, on whose behalf, up to what value and count, in which countries, and until when. Grants can be delegated but never widened. **Approvals** bind a vote to an exact transaction.

**Key test data:**
- Grant to B: `payment.release`, `mandate.create` on `payment:*`, `mandate:*`, `1000.00 GBP`, count 10, R3.
- Approval context: `payment.release` / `payment:pay_123`.

**Advantage:** no ALLOW without a complete chain back to a verified root. Revocation flows down the chain instantly.
**Disadvantage:** complex to learn. It needs a second person, and you must copy the context string exactly (the server keeps only a hash).

### 7.5 Mandates (§4)
**Use:** a **budget** taken from a grant and given to an executor (a person or an **AI agent**): total value, per-transaction value, count and time window. Executors **reserve**, then **commit**.

**Key test data:**
- `GBP`, total `500.00`, per transaction `50.00`, count `5`.
- Reserve `40.00` → commit.
- `60` → `MANDATE_VALUE_LIMIT`.

**Advantage:** hard spending limits, especially safe for AI agents. Balances are live.
**Disadvantage:** only the executor can reserve, and you need a grant with `mandate.create` first.

### 7.6 Policies (§5)
**Use:** **Cedar** policy-as-code releases. Draft → validate (with mandatory allow/deny test cases) → simulate against the active release → independent review → an Approver publishes.

**Key test data:** name `payments`, keep the template.

**Advantage:** rules are tested, reviewed and versioned before they go live.
**Disadvantage:** needs Cedar knowledge. An invalid name makes Save silently do nothing (a defect).

### 7.7 Access decisions (§6)
**Use:** a test console that asks the decision engine "may this person do this?". You see the outcome (Allow / Deny / Step-up / Approval required), the reasons, the policy and the receipt.

**Key test data:** "Release a payment", `payment` / `inv-001`, `300.00 GBP`.

**Advantage:** explains *why*, so it is great for debugging and demos.
**Disadvantage:** a testing tool. Advanced context is raw JSON.

### 7.8 Security signals (§13)
**Use:** share security events (session revoked, credential compromised) with other systems using the SSF/CAEP standards. You can also view or override a principal's security state.

**Key test data:** audience `https://receiver.example.com`, events `session.revoked, credential.compromised`.

**Advantage:** revocation spreads to partner systems within seconds.
**Disadvantage:** "Publish signal" cannot be tested from the console (`SCOPE_INSUFFICIENT`).

### 7.9 People & principals (§9)
**Use:** the list of every person, agent and workload.
- Revoke all of someone's sessions.
- Open **assisted recovery** for someone else.
- Put an account under **lockbox** (recovery needs k of n approvers plus a cooling-off period).

**Key test data:** lockbox with 2 approver UUIDs, k `2`, cooling-off `168` hours.

**Advantage:** strong protection against account takeover of key people.
**Disadvantage:** works with raw UUIDs, and you cannot do it on yourself.

### 7.10 Provisioning (§2)
**Use:** let an HR system or directory create and remove users automatically through **SCIM**, or sync from Auth0.

**Key test data:**
- Source `Workday HR` → **Make a key pair for me**.
- Then the `curl` SCIM create call from the checklist (expect 201).

**Advantage:** automatic joiners and leavers; a leaver loses access when HR says so.
**Disadvantage:** proving it works needs scripts and curl. Directory sync supports Auth0 only.

### 7.11 Evidence & audit (§7)
**Use:** signed **decision receipts** (verify the hash/ledger/checkpoint, replay the decision), signed export packages for auditors, bulk exports, annotations, **legal holds** (two-person rule), cases and provenance.

**Key test data:**
- Export purpose `Customer audit request`, case `CASE-2026-014`.
- Legal hold context `evidence.legal_hold.manage|legal_hold:*|MATTER-001||verification.document`.

**Advantage:** tamper-evident proof that survives privacy erasure and is defensible in court or audit.
**Disadvantage:** legal holds need exact approval strings and two people.

### 7.12 Privacy & data rights (§8)
**Use:** the company's GDPR back office:
- Handle data-subject requests (verify → fulfil → close).
- Retention policies (for example crypto-shred after 365 days).
- Processors and sub-processors.
- Privacy notices.
- DPIA, breaches, erasure.

**Key test data:**
- Request type Access, pack `uk-gdpr`.
- Retention `VERIF_DOCS_365`, 365 days, CRYPTO_SHRED.
- Processor `email-provider`, `Acme Mail Ltd`, `UK_IDTA`.

**Advantage:** deadlines are tracked, and retention and processor changes need a second approver.
**Disadvantage:** the DPIA, Breaches, Erasure, Models, Biometric and Assisted tabs were not traced field by field.

### 7.13 Operator access (§10)
**Use:** control whether **Zoiko staff** may access your data. Approve or deny their requests, and require your approval (lockbox) by jurisdiction.

**Key test data:**
- Jurisdictions `GB, IE`.
- `GBR` → `OPERATOR_SCOPE_INVALID`.

**Advantage:** the customer stays in control of the vendor's access.
**Disadvantage:** the request list is empty unless an operator (port 3100) raises a request.

### 7.14 Residency (§11)
**Use:** where your data lives (home cell, legal pack), plus rules for cross-border transfers.

**Key test data:**
- `local-1`, SRP-1, UK GDPR, `LEGAL-2026-001`.
- Transfer: DC1 → `eu-1`, `FRAUD_REVIEW`, `uk_idta`.

**Advantage:** a transfer is refused unless a legal mechanism allows it.
**Disadvantage:** later changes need a second admin. Locally there is only one cell.

### 7.15 Agents & tools (§19–21)
**Use:** give **AI agents** a governed identity. The chain is: register tools → approved build (image digest) → blueprint release → agent with a sponsor and an operator → attestations → activate.
- **Governance** adds signers, permission changes, limits, monitoring and pause/restore.
- **Readiness** runs the access-review agent wizard.

**Key test data:**
- Tool `evidence.lookup`.
- Image `acme/invoice-agent:1.0.0@sha256:<64 hex>`.
- Blueprint `finance-approval-bot` 1.0.0.
- Agent `Acme Invoice Agent`.

**Advantage:** every agent is traceable to a responsible human, approved code, allowed tools and a budget, and can be paused at once.
**Disadvantage:** many steps need a second person. The governance baseline has no form (API only), and one review link goes to a 404 page.

### 7.16 Workload identity (§12)
**Use:** give servers and agents a **SPIFFE** identity (no static secrets), and quarantine a whole environment if compromised.

**Key test data:** namespace `agents`, Production, ATTEST-2, 90 days. Quarantine `dev`.

**Advantage:** no long-lived machine passwords.
**Disadvantage:** needs an agent with an approved blueprint first.

### 7.17 Ownership chains (§16)
**Use:** record **who ultimately owns or controls** the company (UBO graph), work out which people pass the 25% threshold, and have another person confirm.

**Key test data:**
- Keep the starter JSON.
- Negatives: `[]`, a self-link, 150%, a loop.

**Advantage:** detects cycles and threshold owners automatically.
**Disadvantage:** the graph is entered as JSON.

### 7.18 Company verification: grade (§17)
**Use:** the company's KYB **grade K0–K4**. Record signatory mandates (for example a Director acting under a board minute), appeal, and download a signed summary for a bank.

**Key test data:** another person's UUID, Director, `Board minute 2026-11-01`, Sign an agreement.

**Advantage:** a portable, signed proof of company standing.
**Disadvantage:** it has the **same sidebar name** as 5.4, which is confusing.

### 7.19 Identity proofing cases (§18)
**Use:** the reviewer queue for passport/face checks (two reviewers; the applicant cannot review).

**Disadvantage:** empty locally (no market policy).

### 7.20 Screening (§18)
**Use:** sanctions and PEP screening of people and companies.

**Key test data:** `GB`, KYB, `Acme Widgets Limited`.

**Disadvantage:** "Not available yet" until a market policy and sanction lists are seeded.

### 7.21 Age check (§18)
**Use:** decide whether someone meets an age threshold.

**Key test data:**
- `2015-01-01` → "Under the age".
- `1990-01-01` → "Not decided" (a typed date never passes, by design).
- `1990-13-01` → error.

### 7.22 Chip trust store (§18)
**Use:** manage the country certificates (CSCA master lists) used to check **passport chips** are genuine. The flow is stage → validate → approve (a different person) → activate.

**Disadvantage:** needs real certificate files and a 64-hex fingerprint.

### 7.23 Verification spend (§24)
**Use:** a monthly cap on verification vendor costs.

**Key test data:**
- `2500.50 GBP` → "Monthly limit saved".
- `-5`, `1e3`, `GB` → errors.

### 7.24 Face engine readiness (§24)
**Use:** read-only status of the face-matching engine. Check that the page loads.

### 7.25 Compliance cases (§24)
**Use:** read-only compliance work queues.

**Disadvantage:** workflow-run rows link to the version, not the run.

### 7.26 Source of funds (§24)
**Use:** a future product for proving where money came from.

**Disadvantage:** still in **design**, so creating a case returns `SERVICE_UNAVAILABLE`.

### 7.27 Verification workflows (§22)
**Use:** define a KYC process as a safe state machine (checks → review → decision). It is validated against safety rules, simulated, approved by another person, then retired when replaced.

**Key test data:**
- `"id": "kyc_basic"` → **Check** → "Passes the safety rules".
- Negative: remove `"else"` → error.

**Disadvantage:** JSON editing. There is no UI to start a run (API only).

### 7.28 Developers (§23)
**Use:**
- **Webhooks** (verified by a challenge).
- **Test identities** for sandbox testing.
- **Delivery log** with replay.
- **Usage** and **Rate limits** (read-only).

**Key test data:**
- Webhook `https://webhook.site/<id>`.
- Test identities: prefix `test`, 5, 7 days.

**Limits:** 20 identities per batch and 50 live in total.

**Advantage:** self-service integration testing.
**Disadvantage:** a private IP or `http://` is refused, so local receivers need a public URL.

---

## Part 8. Known issues and local limitations (log these)

| # | Where | Issue |
|---|---|---|
| 1 | Overview / Onboarding | Blocking gates read 0 after INTEGRATING; Launch can show although the server refuses |
| 2 | Onboarding | Hidden PILOT rule: you must roll back once before launch |
| 3 | Company verification | Two sidebar items with the same name; authority page decides nothing; no list of existing claims; GLEIF-only lookups |
| 4 | Domains, Email contacts, Applications (clients), Settings → Suspend | Destructive buttons with no confirmation |
| 5 | Applications | "Keep it simple" can send custom choices; names over about 61 characters fail; no Retire |
| 6 | Integrations | Mapping releases and login routes not used by real sign-in or provisioning yet |
| 7 | Team | No invitation email; template and unit forms give no error text |
| 8 | Verification checks | No adapters configured locally; Purpose is not uppercased |
| 9 | Email contacts | Says "check the new inbox" even when email is not configured |
| 10 | Passport check | Empty market policy locally; passport case shows Aadhaar text (likely bug); no chip/NFC (no mobile app in repo) |
| 11 | Recover | Email recovery template ZID-EM-010 not enabled locally |
| 12 | Alerts | Members see a tile but probably get "Not permitted" |
| 13 | Administration | See the defect list at the end of [admin-manual-test-checklist.md](admin-manual-test-checklist.md) (policy name, delegation Apply button, agent review 404, etc.) |
