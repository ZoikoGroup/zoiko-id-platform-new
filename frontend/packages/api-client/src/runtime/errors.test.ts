import { describe, expect, it } from "vitest";

import { ApiError } from "./errors";

describe("ApiError.fromBody", () => {
  it("reads an RFC 9457 problem from the /v2 surface with the document's public code", () => {
    const e = ApiError.fromBody(422, {
      type: "https://api.zoikoid.com/problems/unsupported-market",
      title: "Verification route unavailable",
      status: 422,
      code: "unsupported_market",
      detail: "No authorized service is available for the requested check in this jurisdiction.",
      trace_id: "req_1",
      correlation_id: "cor_1",
    });
    expect(e.code).toBe("unsupported_market");
    expect(e.message).toBe("No authorized service is available for the requested check in this jurisdiction.");
    expect(e.status).toBe(422);
    expect(e.correlationId).toBe("cor_1");
  });

  it("falls back to the title when a problem has no detail, and still reads the /v1 envelope", () => {
    expect(ApiError.fromBody(409, { type: "x", title: "Idempotency key reused", status: 409, code: "idempotency_conflict" }).message).toBe("Idempotency key reused");
    const v1 = ApiError.fromBody(404, { error: { code: "NOT_FOUND", message: "Resource not found." } });
    expect(v1.code).toBe("NOT_FOUND");
  });

  it("does not mistake an ordinary body for a problem", () => {
    expect(ApiError.fromBody(500, { code: "x" }).code).toBe("SERVICE_UNAVAILABLE");
  });
});
