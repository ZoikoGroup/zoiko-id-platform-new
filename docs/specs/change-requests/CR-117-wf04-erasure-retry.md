# CR-117: automatic retry of a failed first-party erasure

Source: Wireframe 04 (ZID-DTV-WF-04) QA-023. Status: engineering built 18 Nov 2026.

## What was built

- **A store that raises no longer aborts the job.** The disposer runs in a savepoint; a failure becomes a FAILED task (`DISPOSER_ERROR`) and the rest of the job continues.
- **Automatic retry** (`retry_failed_erasures`, run by the worker's `privacy` step): FAILED first-party tasks are retried with a delay of 15 minutes that doubles and never passes a day (`retry_delay_minutes`), at most 5 attempts (`MAX_ERASURE_ATTEMPTS`). Migration `privacy_0011` adds `attempts` and `next_attempt_at` to `privacy.erasure_task`.
- **Never complete without disposal.** A task becomes ACKNOWLEDGED, with a receipt, only when the store actually disposed the data (or the entry was already disposed). A task that runs out of attempts stays FAILED, stops being retried, and keeps the oversight view at `ERASURE_STUCK` until a person acts.
- Tests: `tests/component/test_erasure_retry_pg.py` (delay rule, recovery after the store returns, backoff respected, give-up after the ceiling).

## Not built

- A provider's deletion is not retried by us: the provider has to acknowledge with its signed receipt. There is no automatic re-sending of a deletion request to a provider, because no provider is contracted.
- No alert is pushed when a task gives up; it appears in the oversight view and the stuck count.
