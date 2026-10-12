"""Outbox relay process (Event Platform): FOR UPDATE SKIP LOCKED -> Kafka with an idempotent
producer (DEC-D2). Owns events.outbox_event / events.inbox_message. Same image, separate process."""
