# CR-008: Relationship change event for the projection

- **Target:** ZID-EVENTS-001 §22 (event catalogue) · **Raised by:** DEC-C4 SpiceDB projector · **Status:** Open

ZID-AUTHZ-001 §7.1 requires every material relationship mutation to advance a tenant `relation_revision` and the projection to follow it, and DEC-C4 has the projector consume outbox events. No canonical spec lists a relationship event, so the projector has nothing declared to listen to.

**Proposed:** add `com.zoiko.id.relationship.edge.changed.v1`, aggregate = tenant relation publication, `aggregate_version` = `relation_revision` (monotonic per tenant), payload = `relationship_id`, `relation_type`, `status`, `relation_revision` (opaque ids only). Implemented now under `contracts/asyncapi/proposed-events.yaml`.
