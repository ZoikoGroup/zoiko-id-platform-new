/** Sign-up state S3, change the address (ZID-UX-WF-013A): a new request from the browser that already holds an open one ends the earlier
 * one. The earlier request is named only by this browser's own HttpOnly cookie; whatever the page sends is never believed. */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));
vi.mock("@/server/bff", () => ({
  server: () => ({ env: { cellApiUrl: "http://cell.test", publicOrigin: "http://app.test" }, bff: { device: (r: Request) => resolveDevice(r.headers.get("cookie"), false) } }),
}));

import { resolveDevice } from "@zoiko/auth-bff";

import { get, post, REQUEST_COOKIE } from "./registration";

const FIELDS = ["country", "journey", "email", "locale", "notice_version", "nonce"];
let sent: Record<string, unknown> | null = null;

beforeEach(() => {
  sent = null;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (_url: string, init: RequestInit) => {
      sent = JSON.parse(String(init.body)) as Record<string, unknown>;
      return new Response(JSON.stringify({ request_id: "req_new" }), { status: 202, headers: { "content-type": "application/json" } });
    }),
  );
});
afterEach(() => vi.unstubAllGlobals());

function request(body: Record<string, unknown>, cookie?: string): Request {
  return new Request("http://app.test/api/registration/intents", {
    method: "POST",
    headers: { "content-type": "application/json", origin: "http://app.test", ...(cookie ? { cookie } : {}) },
    body: JSON.stringify(body),
  });
}

const typed = { country: "GB", journey: "PERSONAL", email: "right@example.com", notice_version: "v1" };

describe("changing the address from the same browser", () => {
  it("names the earlier request from the browser's own cookie", async () => {
    await post(request(typed, `${REQUEST_COOKIE}=req_old`), "/v1/registration/intents", FIELDS, { replacesHeld: true });
    expect(sent?.replaces).toBe("req_old");
    expect(sent?.email).toBe("right@example.com");
  });

  it("never believes a request id the page body supplies", async () => {
    await post(request({ ...typed, replaces: "req_someone_elses" }, `${REQUEST_COOKIE}=req_old`), "/v1/registration/intents", FIELDS, { replacesHeld: true });
    expect(sent?.replaces).toBe("req_old");
    await post(request({ ...typed, replaces: "req_someone_elses" }), "/v1/registration/intents", FIELDS, { replacesHeld: true });
    expect(sent).not.toHaveProperty("replaces");
  });

  it("names nothing when the browser holds no request, and other calls never carry it", async () => {
    await post(request(typed), "/v1/registration/intents", FIELDS, { replacesHeld: true });
    expect(sent).not.toHaveProperty("replaces");
    await post(request({ request_id: "req_1" }, `${REQUEST_COOKIE}=req_old`), "/v1/registration/resend", ["request_id"]);
    expect(sent).not.toHaveProperty("replaces");
  });

  it("refuses a request from another origin before anything is sent", async () => {
    const foreign = new Request("http://app.test/api/registration/intents", {
      method: "POST",
      headers: { "content-type": "application/json", origin: "http://evil.test", cookie: `${REQUEST_COOKIE}=req_old` },
      body: JSON.stringify(typed),
    });
    const res = await post(foreign, "/v1/registration/intents", FIELDS, { replacesHeld: true });
    expect(res.status).toBe(403);
    expect(sent).toBeNull();
  });
});

describe("device id", () => {
  let seenHeaders: Headers | null = null;
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_url: string, init: RequestInit) => {
        seenHeaders = new Headers(init.headers);
        return new Response("{}", { status: 202, headers: { "content-type": "application/json" } });
      }),
    );
  });

  it("forwards a minted id upstream and sets the cookie on the response", async () => {
    const res = await post(request(typed), "/v1/registration/intents", FIELDS);
    const id = seenHeaders?.get("x-zid-device");
    expect(id).toMatch(/^[0-9a-f]{32}$/);
    expect(res.headers.get("set-cookie")).toContain(`zid_device=${id}`);
  });

  it("reuses the browser's id, overrides a spoofed header and sets no cookie", async () => {
    const held = "0123456789abcdef0123456789abcdef";
    const req = new Request("http://app.test/api/registration/intents", {
      method: "POST",
      headers: { "content-type": "application/json", origin: "http://app.test", cookie: `zid_device=${held}`, "x-zid-device": "spoofed" },
      body: JSON.stringify(typed),
    });
    const res = await post(req, "/v1/registration/intents", FIELDS);
    expect(seenHeaders?.get("x-zid-device")).toBe(held);
    expect(res.headers.get("set-cookie")).toBeNull();
  });

  it("forwards it on the public reads too", async () => {
    const res = await get(new Request("http://app.test/api/registration/capabilities"), "/v1/registration/capabilities", { country: "GB" });
    expect(seenHeaders?.get("x-zid-device")).toMatch(/^[0-9a-f]{32}$/);
    expect(res.headers.get("set-cookie")).toContain("zid_device=");
  });
});
