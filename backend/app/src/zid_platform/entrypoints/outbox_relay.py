"""Outbox relay process (Event Platform): FOR UPDATE SKIP LOCKED -> Kafka with an idempotent
producer (ADR-0017). Owns events.outbox_event / events.inbox_message. Same image, separate process."""
