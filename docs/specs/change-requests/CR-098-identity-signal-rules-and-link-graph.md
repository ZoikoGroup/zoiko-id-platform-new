# CR-098: Phone, email and device signal rules, and the link graph (ZID-GAP-001 v2.0 DTV-20, DTV-21, DTV-22)

Status: **domain rules and the provider interface built; no provider exists and no route uses them yet.** Nothing here runs for a customer. A data provider, a retention value and a purpose are owner decisions before any of it is wired.

## What was built

`verification/domain/identity_signals.py` (pure):

- **Shape of a phone number and an email address.** E.164 shape for phones; a simplified RFC 5322 shape for email with role-account and plus-address flags. Results say they are SHAPE ONLY: a well-formed number is not a reachable one and a well-formed address is not a deliverable one. A disposable-domain list is the operators' to supply; none ships, so nothing is flagged until someone provides one.
- **A keyed hash for linking.** The tenant and the identifier kind are inside the key, so the same phone in two companies, or a phone and an address that look alike, never link. A pepper is required.
- **The provider interface (`SignalProvider`).** The only place a network call may happen. It receives the normalized value. A provider that raises is UNAVAILABLE, never a pass and never a fail. The shipped provider is `NoProvider` and says `NO_PROVIDER_CONFIGURED`; no risk figure is ever invented.
- **Shared devices.** A device seen with at least five different people is flagged for review. A device seen with more than twenty is a hub (a kiosk, a public computer) and says nothing.
- **The link graph.** People connected by shared identifiers, deterministically (the same input gives the same groups in any order). A hub identifier connects nobody. Strength is only the number of different kinds of identifier that connect a group: one is WEAK, two MODERATE, three or more STRONG.

## The rules (each has a test)

1. A signal is a reason for a person to look. The only recommendation is REVIEW; there is no verdict, no score and no word "fraud".
2. A link is not proof that two people are the same or are acting together, and every link says so.
3. Raw identifiers do not appear in results; the hash does, and it is keyed.
4. A malformed value is reported and is never sent to a provider.
5. Order of arrival never changes the result.

## What it does not do

- It does not look anything up. Whether a number is live, who owns it, whether an address is deliverable and how risky a device is need a provider.
- It stores nothing and sends nothing.
- It makes no decision about a person.

## Owner decisions still needed

1. Which providers (if any), and their data protection terms.
2. The purpose and lawful basis for linking people by shared identifiers, a retention period for the hashes, and a DPIA entry.
3. Whether the fan-out limit (20) and the shared-device minimum (5) are the right values.
4. The operators' disposable-domain list.

## Evidence

`backend/app/tests/unit/test_identity_signals.py`.
