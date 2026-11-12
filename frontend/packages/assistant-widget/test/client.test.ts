import { describe, expect, it } from "vitest";

import { AssistantApiError, createAssistantClient } from "../src";
import { baseRoutes, BASE, err, mockApi } from "./helpers";

describe("typed API client", () => {
  it("calls same-origin only, with credentials same-origin and no uploads", async () => {
    const { fetchImpl, calls } = mockApi(baseRoutes());
    const c = createAssistantClient({ apiBase: BASE, fetchImpl });
    await c.bootstrap();
    await c.createSession("en-US");
    await c.deleteSession();
    expect(calls.map((x) => `${x.method} ${x.path}`)).toEqual(["GET /bootstrap", "POST /sessions", "DELETE /sessions"]);
    for (const call of calls) {
      expect(call.url.startsWith(BASE)).toBe(true); // never an absolute or foreign URL
      expect(call.credentials).toBe("same-origin");
      expect(call.headers["content-type"] ?? "application/json").toBe("application/json"); // JSON only, never multipart
    }
  });

  it("sends the Idempotency-Key on intake and only there", async () => {
    const { fetchImpl, calls } = mockApi(baseRoutes({ "POST /intake/sales": { status: 202, body: { reference: "ZR-1", status: "ACCEPTED" } } }));
    const c = createAssistantClient({ apiBase: BASE, fetchImpl });
    const body = { form_revision: "r", purpose: "sales_inquiry" as const, country_code: "US", locale: "en-US", notice_accepted: true, fields: { email: "a@b.test" } };
    await c.submitIntake("sales", body, "key-123");
    expect(calls[0]?.headers["idempotency-key"]).toBe("key-123");
    await c.createSession();
    expect(calls[1]?.headers["idempotency-key"]).toBeUndefined();
  });

  it("maps a failed network call and a non-JSON 502 to UNAVAILABLE, never to success", async () => {
    const down = createAssistantClient({ apiBase: BASE, fetchImpl: (async () => { throw new TypeError("offline"); }) as unknown as typeof fetch });
    await expect(down.bootstrap()).rejects.toMatchObject({ status: 503, code: "UNAVAILABLE" });
    const html = createAssistantClient({ apiBase: BASE, fetchImpl: (async () => new Response("<html>bad gateway</html>", { status: 502 })) as unknown as typeof fetch });
    await expect(html.bootstrap()).rejects.toBeInstanceOf(AssistantApiError);
    await expect(html.bootstrap()).rejects.toMatchObject({ code: "UNAVAILABLE" });
  });

  it("surfaces the server error code and Retry-After", async () => {
    const { fetchImpl } = mockApi(baseRoutes({ "POST /messages": err(429, "RESOURCE_LIMIT", { retry_after_s: 30 }) }));
    const c = createAssistantClient({ apiBase: BASE, fetchImpl });
    await expect(c.sendMessage({ client_message_id: "1", text: "hi", locale: "en-US" })).rejects.toMatchObject({ status: 429, code: "RESOURCE_LIMIT", retryAfterS: 30 });
  });

  it("builds a link only from a link id", () => {
    const c = createAssistantClient({ apiBase: `${BASE}/` });
    expect(c.linkUrl("DEV DOCS/1")).toBe(`${BASE}/out/DEV%20DOCS%2F1`);
  });
});
