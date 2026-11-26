# Verify API: client certificates, custom domains and logos (product positions)

Written 25 Nov 2026 for the Zoiko iD product owner. For each item: what a partner wants, the position taken, what it needs from outside the application, and what is built. A position is a proposal until the named owner accepts it.

## 1. Client certificates (mTLS) for the token endpoint

**What the partner wants.** Large buyers' security teams ask for mutual TLS because their policy bans shared secrets. The PDF names it as the preferred enterprise option next to a signed assertion.

**Position.** Offer it as an **enterprise option, disabled until a cell is configured for it**, and pin the certificate, never trust a certificate authority. A credential of this kind holds only the SHA-256 thumbprint of the partner's own certificate. The partner's server presents that certificate at the token endpoint, the edge proves the handshake, and the cell compares the thumbprint.

**Why not by default.** A client certificate is checked at the TLS edge, not in the application. The cell can only believe a forwarded certificate if the connection came from a proxy it has been told to trust and that proves itself with a shared secret. If that is wrong, the answer is an authentication bypass. So the cell refuses this credential kind unless all of these are configured, and says which is missing:

1. `ZID_PARTNER_MTLS_PROXIES`: the addresses of the proxies whose forwarded certificate is believed.
2. `ZID_PARTNER_MTLS_PROXY_SECRET_FILE`: a secret of at least 32 characters the proxy sends with each request.
3. `ZID_PARTNER_MTLS_HEADER`: the name of the header the edge puts the certificate in (default `X-Forwarded-Client-Cert`).

**What the infrastructure owner must do.** Terminate TLS at an edge that requests (not requires) a client certificate on `/v1/verify-api/oauth2/token` only; strip any client-supplied copy of the header on every request; add the proxy secret; forward the verified leaf certificate. The application cannot do any of that, and its tests can only simulate it.

**Built.** The credential kind, thumbprint pin, fail-closed gate and tests with a simulated trusted proxy. **Not claimed:** end-to-end TLS handshake testing, which needs the real edge. Decision D08 (credential cryptography profiles) should record that mTLS is an enterprise option and that the private-key assertion remains the default recommendation.

## 2. Custom domains for the verification page

**What the partner wants.** `verify.partner.com` instead of our address, so customers see a familiar host.

**Position: not in version 1.** Reasons a product owner should weigh:

- It is a **phishing risk** surface. A page on the partner's domain that collects face photographs is exactly what a lookalike attacker would register. Hosting it on our own domain, branded with the partner's name, is the safer default and lets customers rely on one address.
- It needs **certificate issuance and renewal for each domain** at the edge, plus DNS ownership proof and revocation handling. That is ingress work, not application code. Building the application half (a DNS record check) without the edge would give a screen that says "verified" and a page that does not load.
- The partner's real need, trust, is met by the **name, accent colour, support link and return address** already configurable (CR-156), and by the logo below.

**What would change this.** An ingress that provisions certificates per verified domain, a rule that a custom domain must pass the same DNS ownership proof as organization domains, a registered return address on the same registrable domain, and an operator approval before go-live. Revisit when a signed partner asks and the edge exists.

**Built.** Nothing, deliberately. This memo is the decision record.

## 3. Logos on the verification page

**What the partner wants.** Their logo at the top, so a customer recognises who is asking.

**Position: yes, with controls, because a logo is how the page can be made to look like someone else.**

- **The page keeps our chrome.** The partner's logo sits in a fixed header slot next to the line "Verification for {name}, processed by Zoiko iD". It can never replace our name or alter the page.
- **Upload, not link.** A linked image lets the partner (or anyone who can change that address later) track every customer who opens the page, and can change after approval. So the logo is uploaded, scanned for malware, re-encoded by the imaging sidecar to a plain image with all metadata removed, size-limited, and served by us.
- **Approval for production.** In the sandbox a logo shows at once with a "Sandbox test" banner. For a production application it shows only after an operator has approved that exact image (identified by its digest); a changed image goes back to waiting. This is the same two-step control used for credentials and prices, and it stops a partner presenting another brand's logo to real customers.
- **Plain fallback.** No approved logo means the partner's name in text. Never an error, never a broken image.
- **Accessibility.** The logo has the partner's name as its text alternative; the name is always present as text.

**Built.** Upload with scan and re-encode, digest-pinned approval, serving on the hosted page, tests. Sandbox and production behaviour as above.

## 4. Summary of positions

| Item | Position | Built |
|---|---|---|
| mTLS | Enterprise option, pinned certificate, disabled until the edge and a trusted proxy are configured | Credential kind and fail-closed gate, simulated tests |
| Custom domains | Not in version 1; safer on our own address with partner branding | Decision record only |
| Logos | Yes, uploaded and re-encoded, operator-approved for production | Yes |
