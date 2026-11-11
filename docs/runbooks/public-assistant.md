# Runbook: public assistant

**Service:** `backend/assistant` (image target `assistant`, port 8210) · **Owner (proposed):** @zoiko/platform with @zoiko/security · **Tier:** proposed T2 (the website must work without it)
**Status:** draft for OD-021/OD-024. It has **not been drilled**; `last_exercised: null` until SRE runs it.

The one rule that governs everything below: **the assistant can always be switched off without touching the website, sign-in or the identity platform** (INV-07). Switching it off is never the wrong first move.

## 0. Emergency stop (kill switch)

Needs `ZID_ASSISTANT_VALKEY_URL`, so every replica sees the change within a few seconds. No deploy, no restart.

```powershell
python -m zid_assistant.flags_cli set kill_switch on     # chat, model and intake all stop
python -m zid_assistant.flags_cli show
python -m zid_assistant.flags_cli clear kill_switch      # restore only after the incident owner approves
```

Effect: `POST /sessions` and `POST /messages` return 503 `UNAVAILABLE`; `/bootstrap` reports `enabled: false` with the **static help links**, so the widget shows only official links; `/out/{link_id}` and `/health/*` keep working. In-flight sessions simply end.

Narrower switches (leave the rest running): `set ai_answers off` (verified source excerpts only, no model), `set intake_sales off`, `set intake_support off`, `set marketing off`.

If the flag store is unreachable, the **configured defaults stand**: a store outage never turns a feature on.

## 1. Withdraw a claim, source or link (wrong or unapproved statement)

Withdrawal is checked at **serving time and at link click time**, so it takes effect on the next request even though the index was built earlier, and on every replica (Valkey), with no deploy.

```powershell
python -m zid_assistant.flags_cli revoke source PUB-005        # a P02 source: answers that rely on it abstain
python -m zid_assistant.flags_cli revoke link OVERVIEW         # a P03 link: no longer offered, /out returns 404
python -m zid_assistant.flags_cli revoke capability CAP-001    # a P01 capability
python -m zid_assistant.flags_cli revoked                      # what is withdrawn right now
python -m zid_assistant.flags_cli restore source PUB-005       # only after Product approves
```

Then make it permanent through the normal change: set the item's state to `WITHDRAWN` / `SUSPENDED` in `content/` (CODEOWNERS-reviewed), sign and roll out the new snapshot, and only then `restore` the temporary entry. A store outage keeps the **last known** list: it never un-revokes. If the statement is harmful and you are unsure what to pull, use the kill switch (section 0).

Verify: ask the question again. A withdrawn source gives `ABSTAIN`; a withdrawn link is not offered and `/out/{link_id}` returns 404.

## 2. A secret or personal data was typed into the chat

The interceptor blocks it **before** any store, log, queue or model sees it (422 `SENSITIVE_CONTENT_BLOCKED`), so there is normally nothing to clean up. To confirm: search logs for the category counters only; there is no text to find. If a gap is suspected (a pattern the interceptor misses):

1. `set ai_answers off` and, if the gap is serious, the kill switch.
2. Escalate to Security as a privacy incident (OD-012). Do not copy the text into a ticket.
3. Add the pattern and a canary test to `tests/unit/test_sensitive.py` and `tests/security/test_p0_scenarios.py` before restoring.

## 3. Model outage, timeouts or runaway cost

Symptoms: `MODEL_UNAVAILABLE` abstentions, rising latency, budget alarms. The gateway already trips a **circuit breaker** (5 failures, 60 s cool-down) and enforces a **daily request budget**; both fall back to verified source excerpts, never to a made-up answer.

1. `set ai_answers off` to stop calling the provider; chat keeps working from approved content.
2. Check provider status and the key file. A refusal is **not** retried elsewhere by design (no fallback to another model, ADR-10).
3. Restore with `clear ai_answers` once calls succeed. Changing the model tier is the setting `ZID_ASSISTANT_MODEL_NAME` (OD-017).

## 4. Intake backlog or failed delivery

`delivery_outbox` rows move `PENDING → DELIVERED | RETRYING | REJECTED | DEAD_LETTER`. A visitor is never told "delivered"; they hold only an opaque reference.

- Backlog: check the receiver (`ZID_ASSISTANT_INTAKE_DESTINATIONS`) and the worker log. Rows retry with exponential backoff, up to `ZID_ASSISTANT_OUTBOX_MAX_ATTEMPTS`.
- `DEAD_LETTER`: the request is safe in the encrypted store; fix the receiver, then re-queue by setting the row back to `PENDING`.
- To stop new submissions immediately: `set intake_sales off` / `set intake_support off`. Visitors then see the static contact links.

## 5. Erase a person's request (rights request)

By opaque reference only (there is no lookup by email): `application.retention.erase_intake(store, "ZR-XXXXXXXX")` nulls the encrypted fields and tombstones the row; an erasure is never overwritten by a later delivery. Record the outcome in the privacy case.

## 6. Health and what "ready" means

`GET /health/ready` checks the database and the `public_assistant` schema. `GET /internal/metrics` is the cell-internal Prometheus scrape. Metrics and logs carry **metadata only** (route, verdict, source ids, timings); no visitor text, no model output, no address.

## 7. Restore from a bad release

Roll back the image (the `assistant` image is independent of the platform image). Content rollback is a snapshot rollback: a previous **signed** snapshot is a valid snapshot. Run the P0 suite (`python -m pytest assistant/tests/security`) against the restored build before re-enabling.
