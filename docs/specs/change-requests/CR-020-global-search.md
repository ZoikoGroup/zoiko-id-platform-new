# CR-020: Global search

- **Target:** ZID-UX-001 §3.1, §25, UX-01; action catalogue · **Raised by:** Phase H H-2 · **Status:** Open

## Tables

None. Search reads each owning module through that module's `ports.py` (`searchable_*`), inside the caller's own tenant transaction (RLS on), so it holds no copy of the data and cannot cross tenants.

## API

`GET /v1/search?q=` returns at most 5 hits per object type (applications, connectors, domains, webhook endpoints, alerts), ranked exact / prefix / word / substring.

## Authorization

Each object type is gated by a read action; a type the caller may not read contributes nothing (no hits, no counts, no sign it exists). Queries shorter than 2 or longer than 80 characters are not searched. No personal data is indexed: only names, domains and URLs of configuration objects.

## Actions

`integration.read` (R1/E1), `tenant.domain.read` (R1/E1); `webhook.endpoint.read` and `notification.alert.read` come from CR-018 and CR-019.
