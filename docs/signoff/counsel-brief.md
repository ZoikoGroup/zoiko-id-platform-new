# Counsel brief: consent and biometric notices, and the Wave 1 legal assessments (EDR06-07, EDR04-07)

**Status: DRAFT BRIEF for counsel. Not legal text, not approved, not in force.** It states what the product does, what each notice must say according to the decisions, and the questions counsel must answer. Counsel writes and approves the notices; engineering will
load only the text a named counsel signs, per country, and the product shows nothing as consent until then.

Sources: ZID-EDR-001 (Global Launch Geography, section 4: the six approvals), ZID-EDR-006 (Required Identity Verification Checks), ZID-ED-04, the progressive verification owner pack. Decision records are "proposed for founder approval" until signed.

## 1. What the product does (so counsel assesses the real thing)
- A person signs up with an email only (risk tier R0): no document, selfie, address or phone is asked for. The account reads "Not identity verified".
- Identity proofing (R1) is a separate, optional step started by the person: a government ID plus a live selfie and liveness check, done by a supplier (Veriff primary, Sumsub secondary, subject to contract and country coverage). The platform keeps the signed result,
  a commitment to the verified name and birth date, the supplier reference, the policy version and the expiry. It does **not** store ID images, selfies or biometric templates by default; those stay in the supplier's protected media.
- Organization authority (R2) is a further, separate decision: company register match, the person's authority, and screening. Identity proofing alone never makes someone an owner or director.
- An approved alternative exists for people without a camera, smartphone or standard ID (attended or manual route). It must not silently lower assurance.
- Residency: identity documents and biometric media are classified in the platform's DC0 to DC3 scheme and kept in the organization's chosen region; there is no silent cross-region supplier fallback.

## 2. What each biometric notice must show before any biometric comparison (ZID-EDR-006 section 04)
Counsel supplies the wording per country; the screen has a place for each of these, in the person's language:
1. The **purpose** (to confirm that the live person is the person on the document, once, for this verification).
2. The **legal basis**, and the **special-category condition** where biometric data is processed for identification.
3. The **supplier** and its role (processor or controller), and where the media is held.
4. **Retention** of the media and of the result, and what is deleted when.
5. The **lawful alternative or review route**, and how to ask for it without penalty.
6. The person's rights: access, correction, appeal of a decision, deletion, and how to complain.
7. A statement that nothing here is required to open an unverified account.

## 3. Consent mechanics engineering will implement once the text is signed
Separate, unticked, granular choice for biometrics; the notice version and the person's choice recorded with the verification session; withdrawal possible and its effect stated; no pre-ticked box; a "decline" that leads to the alternative route, never to a dead end. Minors: the product
does not process a minor's identity or biometrics without specific legal and product approval; counsel to state the age rule per country.

## 4. Questions for counsel, per cluster (one engagement can cover each cluster)
| Cluster | Countries | Questions |
|---|---|---|
| United States | US (state by state) | Which state biometric laws apply to the live selfie and liveness check (for example Illinois BIPA, Texas, Washington), what written release and retention schedule each needs, and whether the supplier's terms satisfy them. Platform role and any money-transmission or licensing position for the commercial features. |
| United Kingdom | GB | UK GDPR basis and special-category condition for biometric matching; DPIA outcome; alternative route; fit with the UK digital identity trust framework; ICO guidance on biometric recognition; supplier transfer position. |
| European Union | IE, DE, FR | GDPR Article 9 condition for biometric identification (explicit consent or another), DPIA, SCCs or adequacy for supplier transfers, member-state variations (Germany, France), the French CNIL position on biometric checks, and the right to a non-biometric alternative. |
| All | each Wave 1 country | Platform role and licensing or registration obligations; permitted data use by the supplier; incident notification duties; retention limits; subprocessor list; the exact notices to publish; support and dispute routes. |

## 5. What counsel returns, and how it is recorded
- A signed legal assessment per cluster, the approved notice text per country and language, the retention schedule and the subprocessor list.
- Each is attached to the go-live board with `tools/scripts/record_evidence.py` (reference, checksum, who, which rows: `legal_assessment:GB`, `privacy_and_data:GB` and so on). Engineering then loads the text into the verification notice configuration for that country only.
- Until then a country's regulated capabilities stay disabled by the country registry, and the sandbox simulator counts only in local and test.

## 6. Out of scope for this brief
Tax and accounting advice, the commercial contract with Stripe, the customer-facing terms of service, and any statement of assurance level: the R0 to R3 tiers are Zoiko's own and are not asserted as NIST IAL levels without a control-by-control assessment.
