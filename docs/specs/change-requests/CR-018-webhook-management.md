# CR-018: Webhook management API and delivery runtime

- **Target:** ZID-EVENTS-001 §14-20; ZID-API-001 §17; ZID-UX-001 §18; action catalogue · **Raised by:** Phase H H-1 (Developer Console) · **Status:** Open

## Tables

No new tables: `events.webhook_endpoint` and `events.webhook_delivery` (owner: webhook) already exist.

## API

`/v1/webhooks/...`: subscribable event types, public signing keys, endpoint register / read / list / verify / pause / resume / retire, delivery log, delivery replay.

## Actions

| Action | Risk / evidence | Note |
|---|---|---|
| `webhook.endpoint.manage` | R2 / E2 | Register, verify, pause, resume, retire endpoints; replay deliveries. |
| `webhook.endpoint.read` | R1 / E1 | Read endpoints, event types and the delivery log. |

## Events

No new event types: `webhook.endpoint.registered`, `.verified`, `.disabled` and `webhook.subscription.changed` are already in the event catalogue.

## Runtime

- The event consumer routes allowlisted integration events to subscribed ACTIVE endpoints (`route`).
- The worker delivers due deliveries with a per-attempt DNS re-check, no redirects and RFC 9421 signatures (`deliver_due`).
- The signing key is `ZID_WEBHOOK_KMS_KEY_ID` (local/test: the development key); receivers fetch the public key from `/v1/webhooks/signing-keys`.
