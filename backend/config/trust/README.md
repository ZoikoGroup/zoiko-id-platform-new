# Trust configuration for document signature checks (ZID-INT-002 section 09)

Nothing here is trusted by default: **this folder ships empty on purpose**. With it empty, a signed PDF's signature is reported as
cryptographically valid but INDETERMINATE (`NO_TRUST_ANCHORS_CONFIGURED`), and qualified status is NOT_ASSESSED.

| Folder | What goes in it | Who decides |
|---|---|---|
| `anchors/` | CA certificates (`.pem`, `.crt`, `.cer`) a signature chain may end at | Security + Legal, per market |
| `trusted-lists/` | ETSI TS 119 612 trusted-list XML files (for the EU, the member-state lists named by the LOTL), downloaded by the operator | Operations, refreshed on a schedule |
| `tl-signers/` | The certificates a trusted list's own XML signature must verify against (published with the EU list of lists) | Security |

A trusted list counts only if its signature verifies against a pinned signer; an unverified list is ignored and reported. Revocation (CRL or
OCSP) is **not** checked by this validator, and the signing time inside a signature is the signer's own claim: a fully valid result is at
most `VERIFIED_LIMITED` and says so. Override the config root with `ZID_PLATFORM_CONFIG`.
