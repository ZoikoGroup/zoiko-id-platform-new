# CR-100: Address proof rules, four regulated shells and ownership-chain rules (ZID-GAP-001 v2.0 DTV-17, DTV-24, DTV-26, DTV-27, DTV-32, DTV-34)

Status: **rules built and tested; nothing here runs for a customer.** Every policy and rule set is SAMPLE content that no owner approved, and no provider is connected. Built on the engineering owner's instruction; the sign-off table below is UNSIGNED.

## Why

The v2.0 gap list asks for address and non-document identity checks, open banking, payment-method ownership, transaction monitoring, on-chain screening and a UBO ownership chain. Each can quietly turn a weak signal into a pass, or an unknown into "clean". This change builds the parts that decide nothing a person should decide, and refuses to guess.

## What was built

- **Address proof (DTV-17)**: `verification/domain/address.py`, `infrastructure/address_policy_registry.py`, `config/address-proof/policy.yaml` (SAMPLE). Parse and compare (SAME, PARTIAL, DIFFERENT; case, accents, punctuation and abbreviations are not differences). A policy as data (document types, maximum age). `assess_proof` returns SUPPORTS, REVIEW or DOES_NOT_SUPPORT and never rises above "document presented". A postcode is checked only where its shape is known. No provider ships.
- **Open banking (DTV-26)**: `open_banking.py`. An assertion is accepted only from a licensed, active provider in the right jurisdiction, with live, bounded consent. A payload carrying account data is refused whole and not echoed.
- **Payment-method ownership (DTV-27)**: `payment_ownership.py`. A processor statement is asserted, never called verified. A card number (Luhn) or a card-shaped key is refused whole and never echoed.
- **Transaction monitoring (DTV-32)**: `transaction_monitoring.py`. Rules are data. An alert names the transactions and carries no amount or counterparty. An alert is a reason to look; it never blocks.
- **On-chain screening (DTV-34)**: `onchain_screening.py`. Address shape checks for the supported chains, a keyed per-company hash, and a provider interface. With no provider, or a failing one, the answer is UNAVAILABLE and a person looks. Never "clean".
- **Ownership chains (DTV-24)**: `organization/domain/ownership.py`. Exact fractions: shares multiply down a chain and add across paths. Loops are refused whole. A company with no recorded owners and the part nobody explains are gaps (UNRESOLVED_CHAIN, UNEXPLAINED_REMAINDER). The result is a list of candidates for a person, never a named owner. The order links were recorded in does not matter.

## What it does not do

- No route, screen or store for any of these yet; they are rule modules with tests.
- No provider for address, open banking, payment processing or on-chain screening.
- No threshold or rule set is an approved one: each is a sample.

## Owner decisions still needed

1. The accepted address documents and their ages per market; whether utility data counts.
2. The open-banking provider list, consent length and the name-match standard.
3. Which card processor statements are acceptable.
4. The monitoring rules and thresholds, and who reviews an alert.
5. The on-chain screening provider and the bands that need a person.
6. The ownership threshold per market and what counts as control by other means.

## Evidence

`backend/app/tests/unit/test_address.py`, `test_regulated_shells.py`, `test_ownership.py`.

## Sign-off (UNSIGNED)

| Role | Name | Date | Decision on the six questions above |
|---|---|---|---|
| Product owner | | | |
| Compliance officer | | | |
