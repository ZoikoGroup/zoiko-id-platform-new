"""The workload attestor: a separate trust boundary that inspects a running workload and signs what it found.

The cell (`POST /v1/machine/attestations:verify`) only verifies statements; it never inspects a runtime and never holds the
attestor's key. That separation is the point: a compromised cell cannot forge an attestation, and a compromised workload
cannot attest itself, because the attestor looks at the runtime (the container runtime or the cluster API) instead of
asking the workload what it is.

Nothing in this package imports the platform's modules; it needs only the signing library and an HTTP client, so it can run
as its own small process beside the node (see `docs/architecture/workload-attestor.md`).
"""
