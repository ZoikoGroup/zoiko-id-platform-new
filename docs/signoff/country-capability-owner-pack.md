# Country capability registry: owner pack (ZID-EDR-001)

What only the owners can record. Engineering has built the registry and enforces it; no country is live because it is listed. The registry is `backend/config/country-capabilities/country-capabilities.yaml`. The build refuses ENABLED for a regulated capability without `effective_from` and all six approvals, each with a recorded reference.

## 1. Go/no-go per country and capability

For each country and each of REGISTRATION, INDIVIDUAL_VERIFICATION, BUSINESS_VERIFICATION, CREDENTIAL_ISSUANCE and ENTERPRISE_USE, record a reference for each approval:

| Approval | Owner | Reference | Date |
|---|---|---|---|
| `legal_assessment` | Legal | ______ | ______ |
| `supplier_coverage` | Product / Trust | ______ | ______ |
| `privacy_and_data` | Privacy | ______ | ______ |
| `security_and_resilience` | Security / SRE | ______ | ______ |
| `local_support` | Support | ______ | ______ |
| `signoff` | Founder | ______ | ______ |

Today only GB has sandbox entries (local and test only). US, IE, DE and the other listed countries have DISCOVERY only; nothing regulated is open for them.

## 2. Providers

A country needs a named provider with a signed contract and keys before its status can change from SANDBOX. None is wired for the U.S. or the EU. Record the provider, the contract reference and the countries it covers (see `identity-suppliers-owner-pack.md`).

## 3. Sign-up rows

`backend/config/registration/country-routing.yaml` carries GB only. An EU or U.S. row is switched on only when its home cell has passed its readiness gate (`regional-residency-owner-pack.md`) and REGISTRATION is approved above.

## 4. Contact route

People in a closed country are shown a contact. The default is `support@zoikogroup.com`; a country can name its own. Support confirms the address is monitored.

## 5. Real-device checks

Automated checks cover Unicode names and the accessibility of the sign-up screen. Someone must still try local address, phone and time-zone entry on real devices when those screens exist.

## 6. Test status

EDR01-01 to EDR01-05 stay IN_PROGRESS in `docs/traceability/acceptance-tests.csv` until an owner accepts them.
