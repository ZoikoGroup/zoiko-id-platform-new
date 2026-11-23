# Identity and company verification: one guided flow for every country, no operator activation

Status: **PROPOSED design, written 23 Nov 2026 on the engineering owner's direction. Nothing here is built, approved or certified.** The part that changes a compliance control is section 6; it needs the owners' written acceptance before real customers use it.

Sources: the reference product's identity wizard and document list (`zoikorooms-finalcheck`: `IdentityVerificationWizard.tsx`, `identity-documents.ts`), this platform's current identity proofing (`account/verification`, `idv/`), company verification (`admin/company-verification`, organization claim, certificate OCR), `backend/config/idv/market-policies.yaml`, `country_gate.py` and the operator Regulatory pages.

## 1. What the person experiences

One path, the same in every country. Nobody on the operator side has to open, release or activate anything for it to work.

1. **Sign in** at the company site (`id.<domain>`).
2. **Identity verification** (section 3): a six-screen wizard. The person verifies themselves.
3. **Company verification** (section 4): the person uploads the company's certificate of incorporation, the platform reads it (OCR), and the person confirms what was read.
4. **Onboarding** (section 5): when both are done the person is taken there automatically.

Nothing on the page can mark anyone verified: the server decides every result. (As built, the details and photographs are held in the browser until the person submits; a submitted case is kept on the server and can be resumed. See section 10.)

## 2. Principles

- **One flow, all countries.** The country a person lives in does not change which screens or documents they see.
- **A person finishes on their own.** The operator console is for exceptions only (section 7).
- **Every result is recorded with the policy that produced it** (its version and digest), so a decision can always be explained later.
- **Fail closed.** A step the platform cannot complete (a provider down, a file it cannot read) leaves the person on that step with a plain message. It never approves by default.

## 3. Identity verification: six screens

Modeled on the reference wizard (intro, details, how it works, review, photos, result), with a progress bar ("Step n of 6"), a Back button on every step, and focus moved to each new heading for keyboard and screen-reader users.

| # | Screen | What it holds |
|---|---|---|
| 1 | **Verify your identity** | What you will need, how it works in three lines, a privacy note. |
| 2 | **Confirm your details** | Given name, middle names (optional), family name, date of birth, and "country you live in" (a label for the record, **not** a gate). |
| 3 | **How verification works** | Three numbered blocks: photograph your document, take a selfie, we compare the two. The consent text. "Continue on my phone" gives a single-use link. |
| 4 | **Choose and upload your documents** | See below. |
| 5 | **Review and continue** | The details and the files, with a statement the person attests to. |
| 6 | **Result** | Verified, In review (a person will look at it), or Action needed (with the reason and a way to start again). |

### Documents: all of them, for every country

Shown in three groups with a one-line hint each. The person picks what they hold. This is the reference's list.

| Group | Hint | Documents |
|---|---|---|
| Identity document | Issued by a government, proves who you are | Passport, driving licence, national ID, Aadhaar, PAN card, voter ID, residence permit, permanent resident card, government photo ID, government employee ID |
| Address or residency | A bill, statement or agreement that proves where you live | Electricity, water, gas, telephone and internet bills, property tax bill, bank statement, credit card statement, government address certificate, rental agreement |
| Other | Anything else | Birth certificate, marriage certificate, other government document, other |

- **Required:** one identity document and a selfie. Address and other documents are optional extras.
- **Files:** PDF, JPG or PNG, up to 10 MB each (the reference's limits; confirm against the platform's upload limits when building). Every upload goes through the malware scanner first. A file that cannot be scanned is refused.
- **Utility bills are address evidence only**, never an identity document, exactly as in the reference.

### Decision

The platform compares the face on the identity document with the selfie.
- **Score at or above the line:** Verified, at the grade the route earns (document plus face match, no liveness test: the lowest grade today).
- **Score below the line, a file it cannot read, or an expired document:** In review. A person looks at it (section 7).
- The line is a setting an owner chooses. Nothing in this design sets it.

## 4. Company verification

Opens only after the person's identity is Verified. Until then the page says what to finish first and links to it.

1. **Upload** the certificate of incorporation (PDF, JPG or PNG). Other company documents are optional.
2. **Read it:** the OCR service extracts the company name, number, date of incorporation, country of registration and, where printed, the registered address.
3. **Confirm:** the person sees what was read next to the details they entered, and corrects or confirms each field.
4. **Check:** the existing two-check claim compares the OCR result with the confirmed details (and the company register where one is reachable).
5. **Result:** Verified, or In review when the two disagree or the file cannot be read.

The person who verifies the company must be an Owner of it (the platform already ties company actions to the Owner).

## 5. Onboarding hand-off

When identity and company are both Verified, the page shows "You are verified" and takes the person to onboarding. If only one is done, the home page shows a two-line checklist with what is left. A person never lands on onboarding while a step is open.

## 6. Policy: a built-in global policy replaces per-country packs

**What changes.** Today each country needs a signed, released policy before a case can run, and the policy file says why: *"a case for a market that is not listed here is refused, never run on a default."* This design replaces that with **one built-in global policy per capability** (identity, company). It is a reviewed file in the repository, in force automatically, and recorded on every case. No operator releases, approves or activates it.

**What does not change.** The decisions a country-specific policy carried are still decisions. They move into the global policy as stated values, each with a named owner, and production refuses to start without the owners' written acceptance:

| Value | Why it is a decision | Owner |
|---|---|---|
| How long document photographs are kept (1 to 90 days; unset means none is kept and the route refuses) | Privacy and retention law | Privacy |
| How long an approval counts as an identity claim (1 to 730 days) | Downstream screening and company checks rely on it | Compliance |
| Biometric basis: lawful basis, impact-assessment reference, notice version | The selfie comparison is biometric processing | Privacy and Legal |
| Expired document: refuse, review or accept | Differs by country today | Compliance |
| Face-match line, and what happens below it | Sets how many people reach a reviewer | Compliance |
| Review deadline for exceptions | The reviewer queue's overdue marker | Operations |

**How it is switched on.** A single production setting names the owners' acceptance, in the same style as the platform's other acknowledgements (for example `ZID_IDV_FACE_ROUTE_ACK`). Without it the cell refuses to start the flow. An operator **cannot activate** the policy; an operator can **suspend** it (a stop, with a reason), after which cases wait. Resuming needs the same two-person rule the platform already uses.

**What this does not make true.** The result is the lowest assurance grade the platform has: a document photograph and a face match with no liveness test. It is not a chip read and not a reviewed identity claim. The reference product's "verified" must not be read as stronger than that. Customers who need more can still use the stronger routes when an owner adds them.

## 7. The operator side after this

| Stays | Goes |
|---|---|
| **Case review** for exceptions: a person looks at an In-review identity or company case and rules on it (two reviewers, as the platform requires). | Regulatory Packs, Releases, check-method approvals and the company-verification policy "Activate now" **for these two capabilities**. |
| **Suspend** the global policy, with a reason. | Any per-country screen a person must open before another person can verify. |
| Incidents, break-glass, recovery, commercial: unchanged. | |

Screening (sanctions and watch lists) is a separate control and is **not** removed by this design.

## 8. What has to change in the code

| Area | Change |
|---|---|
| `backend/config/idv/` | A global policy file and a loader that treats it as in force; the per-market file stays for countries that need stricter rules. |
| `country_gate.py` and the market-release records | The identity and company checks stop asking for a per-country release. The commercial market gate for paid calls is a different gate and needs its own decision (section 9). |
| Identity API | A flow with the six screens' states saved on the server; document and selfie upload; the face comparison; the review queue entry. |
| Company API | Certificate upload, OCR extraction, the confirm step, the two-check claim, the review queue entry. |
| Web: `account/verification` and `idv/` | The six-screen wizard, the document groups, the result pages, "continue on my phone". |
| Web: `admin/company-verification` | The four-step page and the "finish identity first" state. |
| Web: onboarding | The hand-off and the checklist. |
| Operator console | Remove the screens in section 7; keep Case review and add Suspend. |
| Deployment | The acceptance setting in `env.example` and the VM compose. |

Build order: the policy model and the identity flow first, then company, then onboarding, then removing the operator screens, then one rebuild.

## 9. Open decisions and limits

- **Written acceptance of section 6** by Privacy, Legal and Compliance. Without it this is a design, not a release.
- **The commercial market gate** (paid calls per country) is separate. Decide whether it also becomes open.
- **No liveness test.** A selfie can be a photograph of a photograph. The platform's authenticity checks reduce this; they do not remove it.
- **Document types differ in what they can prove.** An address bill is evidence of an address, not of identity, and the flow never treats it as an identity document.
- **Storage:** uploaded documents are encrypted and kept only for the retention period. The retention value must be set before the flow runs.
- **Not designed here:** the stronger routes (chip read, reviewed identity), recovery of a locked-out person, and changes to sign-in.

## 10. As built (23 Nov 2026), and what is not built

Built and tested (backend: 17 unit and 6 PostgreSQL component tests; web and operator: the wizard, journey and retired-screen tests all pass):

- **Global policy.** `backend/config/idv/global-policy.yaml` (values PROPOSED, not accepted by any owner), the loader, and the fallback in `idv_cases.py`: a country with no policy of its own is served by it **only** when `ZID_IDV_GLOBAL_POLICY_ACK` equals `accept-global-idv-policy`. A country's own policy always wins, a malformed file refuses, and the face route still needs `ZID_IDV_FACE_ROUTE_ACK`. Without the setting nothing changes from before.
- **Identity wizard** (`account/idv/identity-wizard.tsx`): the six screens, the three document groups, the same list for every country, JPEG or PNG photographs, a live selfie, the typed details compared once with the document and not stored, the result card, and `?next=` return.
- **Company journey** (`admin/company-verification/verification-journey.tsx`): identity, then company (the existing organization claim with certificate reading), then onboarding; each step opens only when the one before is done. The onboarding page now sends people to it.
- **Operator side:** the menu entries and screens for Policy packs, Market releases, Company verification packs and Check methods are retired (a notice and a link to Case reviews). Their code and records are kept.

**Not built, and why it matters:**

- **Address and other documents are not collected.** They are shown as "not part of this check". Collecting them needs a new upload endpoint and storage; the existing proof-of-address checks are the operator-gated check methods this design retires. Decide where they belong before building.
- **PDF identity documents.** The platform reads JPEG and PNG for identity photographs; the reference accepted PDF too. Photograph limit is the platform's 6 MB, not the reference's 10 MB.
- **"Continue on my phone"** and **progress saved on the server at every step** (the reference has both). Today a refresh before submitting loses what was typed.
- **Operator Suspend** for the global policy. The only stop today is removing `ZID_IDV_GLOBAL_POLICY_ACK` and restarting. A console stop with a reason, and a two-person resume, are not built.
- **The company check itself was not changed.** It already works without an operator pack: a missing pack only means proof retention falls back to the proof policy's own period. The company step reuses it.
- **The commercial per-country gate** (`country_gate.py`, paid calls) is untouched.
- **A mismatch's route to Case reviews** relies on the platform's existing review routes. It was not re-tested end to end on the running stack.
