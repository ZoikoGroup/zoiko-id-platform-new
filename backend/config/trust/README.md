# Trust configuration for document signature checks (ZID-INT-002 section 09)

Nothing here is trusted by default: **this folder ships empty on purpose**. With it empty, a signed PDF's signature is reported as
cryptographically valid but INDETERMINATE (`NO_TRUST_ANCHORS_CONFIGURED`), and qualified status is NOT_ASSESSED.

| Folder | What goes in it | Who decides |
|---|---|---|
| `anchors/` | CA certificates (`.pem`, `.crt`, `.cer`) a signature chain may end at | Security + Legal, per market |
| `trusted-lists/` | ETSI TS 119 612 trusted-list XML files (for the EU, the member-state lists named by the LOTL), downloaded by the operator | Operations, refreshed on a schedule |
| `tl-signers/` | The certificates a trusted list's own XML signature must verify against (published with the EU list of lists) | Security |
| `csca/` | Country signing CA certificates (`.pem`, `.crt`, `.cer`) a passport or eID chip's Document Signer may chain to (`POST /v2/documents/chip-verify`). A country publishes them in a CSCA master list; the operator extracts the certificates and checks them against the country's own publication before placing any here | Security + Legal, per country |

A trusted list counts only if its signature verifies against a pinned signer; an unverified list is ignored and reported. Revocation (CRL or
OCSP) is **not** checked by this validator, and the signing time inside a signature is the signer's own claim: a fully valid result is at
most `VERIFIED_LIMITED` and says so. Override the config root with `ZID_PLATFORM_CONFIG`.

## Filling it from the EU lists (operator tool)

    python -m zid_platform.modules.evidence.infrastructure.trust_refresh --lotl-signer eu-lotl-signer.pem [--anchors-from-lists]

`eu-lotl-signer.pem` is the certificate that signs the EU list of lists. Take it from the Official Journal of the European Union and compare its
SHA-256 fingerprint (the tool prints it) before you rely on anything: no tool can establish that for you, and this one refuses to run without it.
The list of lists is verified against it, each national list against the signers the verified list of lists names, and a list that does not
verify is not written. `--anchors-from-lists` also writes the granted qualified-certificate CAs to `anchors/` (a policy decision: opt in).
`tsa-roots/` and `crls/` are filled by hand; the service never fetches anything. Nothing is deleted.
