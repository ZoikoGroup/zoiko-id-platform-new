# Person-first sign-up: what only the owners can close (ZID-UX-WF-013A; slice L-11, CR-048)

1. **Founder approval** of the 13A wireframe and its acceptance tests (the document is proposed).
2. **Seed and configure the registration tenant per cell:** run `python -m zid_platform.entrypoints.seed_registration_tenant --apply`, then set `ZID_REGISTRATION_TENANT_ID`. Person-first sign-up is off without it.
3. **Country table** (`backend/config/registration/country-routing.yaml`): ruled GB only on 2026-11-02. Legal and Privacy confirm that, and decide when each EU or U.S. row is switched back on (it needs its cell to pass the readiness gates first).
4. **Verification mail:** enable template ZID-EM-001 (and later 002 and 003), route `single_use_verification_url` to the web verify page, and approve the sender identity and copy. Without a route and an enabled template, no mail is sent.
5. **Notice and legal copy:** Terms of Service and Privacy Notice versions and links; which version string is current (`ZID_TENANT_SIGNUP_NOTICE_VERSION`).
6. **Anti-abuse proportionality:** the signed nonce, per-client and per-address limits are engineering defaults; Security decides any further bot control (it must not exclude assistive technology) and sets the edge limits.
7. **SSO providers:** ruled none for now (2026-11-02). Approving Microsoft or Google later needs real provider apps, contracts and a policy for what an SSO email claim may prove.
8. **Approved alternative to a passkey:** the governed recovery path is the only fallback built (never email or SMS). Security and Product approve its wording and assurance policy.
9. **Held requests:** ruled read-only lookup only (2026-11-02). Who in support answers the person, and what they may say, is still to be written (the record forbids revealing scores).
10. **Retention:** ruled 30 days after a request ends (2026-11-02); Privacy confirms the lawful basis, and how long the account index (keyed hash to account) is kept after an account closes.
11. **Browser, screen-reader and device evidence** for AC-10 (320 px, landscape, 200 percent zoom, announced errors), run by QA.
12. **Threat model** (enumeration, replay, injection, account collision, SSO confusion, residency), owned by Security, Privacy and QA.
