# CR-112: the INPI register adapter and the enabled-source gate

Source: Wireframe 02 (ZID-DTV-WF-02) KYB-03, T03, T35. Status: engineering built 18 Nov 2026. The INPI adapter has not been run against a live account.

## What was built

- **The enabled-source gate (T35).** `Routing.enabled(adapter_id)` is true only for a source the routing marks `ENABLED`. `kyb.decide` asks it before any call, for the country's sources, the exception suppliers and the LEI reference. A source marked `DISABLED_UNTIL_CONTRACT`, `DISABLED_UNTIL_CREDENTIALS`, `NOT_QUALIFIED`, `NOT_LICENSED` or not listed is never called, so no request leaves and no billable check is recorded, even if an adapter for it was wired by mistake.
- **`InpiKyb`** (`organization/infrastructure/kyb_adapters.py`): the French RNE API behind an account (`inpi-rne-username` and `inpi-rne-password` in the secret broker). With no credential it makes no request and says the source is unavailable (T03). An answer for another SIREN is never taken for this one, and a dissolved company reads inactive. It is registered in `default_adapters` and listed in `config/kyb/kyb-routing.yaml` as `DISABLED_UNTIL_CREDENTIALS`, in the French exception route, so nothing changes until an owner enables it.

## Not built or not proved

- The adapter's field names follow the published RNE documentation and are unverified against a live account.
- The French first pass stays the open Sirene source; INPI is asked only after an owner enables it and a review trigger fires.
