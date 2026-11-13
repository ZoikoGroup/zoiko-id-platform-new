# CR-086: Enforce-readiness report

Status: open (built and tested; switching any check to enforce stays the owners' decision).

## Why
Every ZID-RCG-003 check ships in `shadow` (ZID_REGULATORY_PACKS, ZID_MARKET_RELEASES, ZID_PROVIDER_ENTITLEMENTS, ZID_PRIVACY_OVERLAY,
ZID_EUDI_WALLET, ZID_MONITORING_SCOPE). Before anyone sets one to `enforce` they need to see what it would refuse today, market by market, and
which owner-supplied configuration (claim templates, privacy overlays, approvals) is still empty. Section 13: global availability is earned
market by market.

## What changes
`GET /v1/regulatory/readiness` (operator plane, any regulatory read capability). For every country in the country registry and every regulated
capability: today's availability, the configured provider, and each refusal enforce would make (`pack:`, `release:`, `entitlement:`,
`suspended:`, `privacy:`), plus whether ongoing sanctions monitoring is approved there. It also returns the current mode of every switch and the
list of empty configuration files. It reads only; nothing is changed or recorded.

The operator console's Regulatory > Readiness screen shows it.

## Not in this change
- Turning any switch to `enforce`: an owner decision per environment.
