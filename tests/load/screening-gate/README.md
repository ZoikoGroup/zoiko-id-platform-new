# Screening performance gate (WF06 ENG-19)

A k6 harness for the tenant screening API: eligibility reads and case opens. It is bound to `contracts/baseline/slo.yaml` (`screening_gate`) by `backend/app/tests/unit/test_load_gate.py`.

**The numbers are proposals.** No requirement sets them, so they carry no `requirement:` key and the owners must ratify them before this is a gate. Nothing here has been run at the gate rate: see `RESULTS.md`.

What it checks: p95 of each call, the error rate, and that no response carries a list name, a candidate or a score (a leak counts as a failure however fast it was). It does not call a licensed provider: use a PERF tenant whose market enables only official sources, so nothing is billed.

Run a smoke first (`-e SMOKE=1`), then the full soak against a **deployed PERF cell**, never production. Resilience under a lost dependency is covered by the chaos suites listed in `tests/chaos/README.md`, not here.
