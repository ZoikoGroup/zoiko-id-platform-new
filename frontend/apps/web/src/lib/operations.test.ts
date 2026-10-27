/** UX-29: a long operation (an export, a replay, a verification) is followed by its state; a change is never silently
 * sent again. The client never retries a mutation by itself, and never retries a refusal. */
import { ApiError } from "@zoiko/api-client";
import { describe, expect, it } from "vitest";

import { makeQueryClient } from "./runtime";

describe("UX-29 long operations use operation state, not blind retry", () => {
  const client = makeQueryClient(() => undefined);

  it("never retries a mutation by itself", () => {
    expect(client.getDefaultOptions().mutations?.retry).toBe(false);
  });

  it("reads are retried at most twice, and never after a refusal", () => {
    const retry = client.getDefaultOptions().queries?.retry as (n: number, e: unknown) => boolean;
    const refused = new ApiError({ status: 403, code: "PROTECTED_ACTION_DENIED", message: "no", requestId: "r", retryable: false } as never);
    const outage = new ApiError({ status: 503, code: "SERVICE_UNAVAILABLE", message: "down", requestId: "r", retryable: true } as never);
    expect(retry(0, refused)).toBe(false); // an answer, not a glitch
    expect(retry(0, outage)).toBe(true);
    expect(retry(2, outage)).toBe(false); // bounded
  });
});
