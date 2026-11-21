# CR-141: a company can get a public key into the API client form without our help, and a private key never reaches us

Source: owner request, 20 Nov 2026 ("for a real customer, how can they fill in these details?"). Builds on CR-093 (API-based agents) and CR-094 (the API agent credential path). Status: built 20 Nov 2026. UI and documentation only; no server rule changed.

## The gap

The Register an API client form asks for a public key set (JWKS) and says "public keys only", but a company had no way to make a key, and the form accepted nothing except hand-written JSON that already had a `kid`. A customer holding the file `openssl ... -pubout` writes, or a key exported from a cloud key service, could not use it. The two risks: a customer gives up (or asks Zoiko to generate the key, so Zoiko would hold a private key), or pastes the wrong half.

## Decision

The customer makes the key pair and keeps the private half. Zoiko never generates, holds or can recover a customer's private key. The form meets the customer where they are:

1. **It accepts what a customer actually has**: a key set, a single public key, or a PEM public key (`-----BEGIN PUBLIC KEY-----`). The browser converts it and, where a key has no `kid`, names it with its RFC 7638 fingerprint (the same value the server computes), so the same key always gets the same name. Members the server does not use are dropped. What is registered is shown back in the box before it is sent.
2. **It refuses a private key at every wall**: a PEM private key (any label), a JWK with a private member, a certificate, a key given as an address (`jku`, `x5u`, `x5c`), an encryption key, a shared secret, an EC curve other than P-256 or P-384, and RSA under 2048 bits. The refusal never repeats the secret. The server applies the same rules again and is the one that decides.
3. **It says how to get a key, safest first**: a key in the company's own key service (AWS KMS, Google Cloud KMS, Azure Key Vault, an HSM), where the private key never leaves; then two `openssl` commands; then a JSON key set. The guide is a native disclosure on the form, so it works with a keyboard and a screen reader.
4. **For a test or a small team, "Make a key pair here"**: the browser (Web Crypto, P-256) makes the pair, saves the private half as a file on the person's computer once, and puts only the public half in the box. The page keeps no copy and sends nothing. The form says plainly that Zoiko has no copy and cannot recover it, and that anyone holding the file can act as the agent.

## Where this stops

- The private key a browser makes exists in that browser for a moment. A company that cannot accept that makes the key in its key service, which is the first thing the guide recommends. This is not offered as the production route.
- An API client still proves only that it holds a key, not what code runs behind it (CR-093). Its tools stay capped at risk class 1 and side effects NONE or REVERSIBLE. Nothing here changes that.
- Changing a key is still a new registration with its own second-person approval.
- The server's own checks (`jwks_problems`) are unchanged; the browser's are a courtesy and a first wall, not the decision.

## Built

`frontend/apps/web/src/features/admin/agents/api-client-key.ts` (conversion, naming, checks, key generation), `api-agent.tsx` (the form: guide, one "Public key" box, "Make a key pair here", note of what was converted), and the operator guide section "Registering an API client: where the key comes from".

Tests: `api-client-key.test.ts` (real keys from Web Crypto: PEM for P-256, P-384, Ed25519 and RSA 2048 convert; the fingerprint equals an independent RFC 7638 computation; RSA 1024, every private form, certificates and remote references are refused and not repeated; a generated pair signs with the file and verifies with the registered key and the key set has no private member) and `api-agent-dialog.test.tsx` (what the form sends for a pasted PEM, a refused private key makes no call, the guide is on the form). The earlier `api-agent.test.ts` still passes unchanged.

## Not done

- A command-line helper that prints a key set from a key file. `openssl` plus the PEM paste covers it.
- Importing a key from a key service by its address. A key must be written out in full; a key fetched from a web address could change after approval, which is why the server refuses it.
