# CR-019: Alerts and tasks (notification centre)

- **Target:** ZID-UX-001 §3, §20, §27; ZID-DATA-001 §11.1; action catalogue · **Raised by:** Phase H H-2 · **Status:** Open

## Tables

| Table | Owner | Note |
|---|---|---|
| `platform.notification_request` | notification | The tenant's in-app alerts and tasks: severity (CRITICAL / HIGH / MEDIUM / INFO), state (OPEN / ACKNOWLEDGED / RESOLVED), link to the object and a recommended safe action. One live row per `correlation_key`: repeats fold into it as an occurrence count (alert-fatigue rule). Mandatory notices (CRITICAL, HIGH) cannot be suppressed. RLS on `tenant_id`. |

## API

`/v1/alerts`: list, summary, read, `:acknowledge`, `:resolve`. Acknowledging is never resolving: both states and actors are kept.

## Actions

`notification.alert.read` (R1/E1) and `notification.alert.manage` (R1/E1; each acknowledge and resolve also leaves an E1 audit record).

## Events

No new event types. The event consumer derives alerts from catalogued tenant-facing events by fixed templates (`modules/notification/domain/alerts.py`); operator-plane events are never turned into customer alerts. Alert text carries no personal data.
