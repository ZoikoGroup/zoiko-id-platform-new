/** The assistant proxy is an allowlist, not a pass-through (ZI-CHAT-ADR-006 ADR-02/06/07). */
import { describe, expect, it, vi } from "vitest";

import { createAssistantProxy, upstreamFromEnv } from "./assistant-proxy";

const UP = "http://assistant.test:8210";

function upstreamReturning(res: () => Response) {
  const f = vi.fn(async (...args: [RequestInfo | URL, RequestInit?]) => (void args, res()));
  return { f, fetchImpl: f as unknown as typeof fetch };
}
const sent = (f: ReturnType<typeof upstreamReturning>["f"]) => {
  const [url, init] = f.mock.calls[0]!;
  return { url: String(url), init: init as RequestInit, headers: init!.headers as Headers };
};
const json = (body: unknown, status = 200, headers: Record<string, string> = {}) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json", ...headers } });
const req = (method: string, path: string, init: RequestInit = {}) => new Request(`http://app.test/api/public/chat/v1${path}`, { method, ...init });

describe("what is forwarded to the assistant", () => {
  it("forwards only the allowlisted headers and the single session cookie", async () => {
    const u = upstreamReturning(() => json({ ok: true }));
    const proxy = createAssistantProxy({ upstream: UP, fetchImpl: u.fetchImpl });
    await proxy(
      req("POST", "/messages", {
        headers: {
          "content-type": "application/json",
          accept: "application/json",
          "accept-language": "en-US",
          origin: "http://app.test",
          "idempotency-key": "k1",
          authorization: "Bearer SECRET-TOKEN",
          "x-forwarded-user": "someone",
          "x-zid-path": "/overview",
          cookie: "session=ZID-SESSION; zid_pa_session=abcdefgh12345678; zid_csrf=xyz",
        },
        body: JSON.stringify({ text: "hi" }),
      }),
      ["messages"],
    );
    const { url, headers } = sent(u.f);
    expect(url).toBe(`${UP}/api/public/chat/v1/messages`);
    expect([...headers.keys()].sort()).toEqual(["accept", "accept-language", "content-type", "cookie", "idempotency-key", "origin"]);
    expect(headers.get("cookie")).toBe("zid_pa_session=abcdefgh12345678"); // no other cookie, ever
    expect(headers.get("authorization")).toBeNull();
  });

  it("forwards only the last hop of x-forwarded-for, and only when it is an address", async () => {
    const u = upstreamReturning(() => json({}));
    const proxy = createAssistantProxy({ upstream: UP, fetchImpl: u.fetchImpl });
    await proxy(req("POST", "/sessions", { headers: { "content-type": "application/json", "x-forwarded-for": "6.6.6.6, 203.0.113.9" }, body: "{}" }), ["sessions"]);
    expect(sent(u.f).headers.get("x-forwarded-for")).toBe("203.0.113.9"); // the spoofable first value is ignored
    const u2 = upstreamReturning(() => json({}));
    await createAssistantProxy({ upstream: UP, fetchImpl: u2.fetchImpl })(req("POST", "/sessions", { headers: { "content-type": "application/json", "x-forwarded-for": "<script>" }, body: "{}" }), ["sessions"]);
    expect(sent(u2.f).headers.get("x-forwarded-for")).toBeNull();
  });

  it("sends no cookie when the browser has no session cookie, and drops the query string", async () => {
    const u = upstreamReturning(() => json({}));
    await createAssistantProxy({ upstream: UP, fetchImpl: u.fetchImpl })(req("GET", "/bootstrap?evil=1&redirect=https://x.test", { headers: { cookie: "auth=secret" } }), ["bootstrap"]);
    const { url, headers } = sent(u.f);
    expect(url).toBe(`${UP}/api/public/chat/v1/bootstrap`);
    expect(headers.get("cookie")).toBeNull();
  });

  it.each([
    [["../v1/messages"]],
    [["messages", ".."]],
    [["admin"]],
    [["oauth2", "token"]],
    [["sessions", "extra"]],
    [["out", "A/B"]],
    [["out"]],
    [["out", "x".repeat(65)]],
    [["intake", "marketing"]],
  ])("refuses a path that is not an assistant route: %j", async (segments) => {
    const u = upstreamReturning(() => json({}));
    const res = await createAssistantProxy({ upstream: UP, fetchImpl: u.fetchImpl })(req("GET", "/x"), segments as string[]);
    expect(res.status).toBe(404);
    expect(u.f).not.toHaveBeenCalled();
  });

  it("refuses a method a route does not allow", async () => {
    const u = upstreamReturning(() => json({}));
    const proxy = createAssistantProxy({ upstream: UP, fetchImpl: u.fetchImpl });
    expect((await proxy(req("POST", "/bootstrap", { body: "{}", headers: { "content-type": "application/json" } }), ["bootstrap"])).status).toBe(405);
    expect((await proxy(req("GET", "/messages"), ["messages"])).status).toBe(405);
    expect((await proxy(req("DELETE", "/messages"), ["messages"])).status).toBe(405);
    expect(u.f).not.toHaveBeenCalled();
  });

  it("accepts JSON only and never a multipart upload", async () => {
    const u = upstreamReturning(() => json({}));
    const res = await createAssistantProxy({ upstream: UP, fetchImpl: u.fetchImpl })(
      req("POST", "/messages", { body: "--b\r\ncontent-disposition: form-data\r\n\r\nx\r\n--b--", headers: { "content-type": "multipart/form-data; boundary=b" } }),
      ["messages"],
    );
    expect(res.status).toBe(415);
    expect(u.f).not.toHaveBeenCalled();
  });

  it("refuses an oversized body", async () => {
    const u = upstreamReturning(() => json({}));
    const res = await createAssistantProxy({ upstream: UP, fetchImpl: u.fetchImpl })(
      req("POST", "/messages", { body: JSON.stringify({ text: "a".repeat(70_000) }), headers: { "content-type": "application/json" } }),
      ["messages"],
    );
    expect(res.status).toBe(413);
    expect(u.f).not.toHaveBeenCalled();
  });
});

describe("what comes back to the browser", () => {
  it("relays only the assistant session cookie, rebuilt as a host-only HttpOnly cookie", async () => {
    const u = upstreamReturning(() => {
      const h = new Headers({ "content-type": "application/json", "x-powered-by": "secret-stack", server: "uvicorn", "x-request-id": "r1" });
      h.append("set-cookie", "zid_pa_session=opaqueHandle1234; Path=/; Domain=.zoiko.com; SameSite=None; Max-Age=7200");
      h.append("set-cookie", "tracker=abc; Path=/");
      h.append("set-cookie", "session=hijack; Path=/");
      return new Response(JSON.stringify({ idle_seconds: 1800 }), { status: 201, headers: h });
    });
    const res = await createAssistantProxy({ upstream: UP, fetchImpl: u.fetchImpl, secureCookies: true })(req("POST", "/sessions", { body: "{}", headers: { "content-type": "application/json" } }), ["sessions"]);
    expect(res.status).toBe(201);
    const cookies = res.headers.getSetCookie();
    expect(cookies).toEqual(["zid_pa_session=opaqueHandle1234; Path=/api/public/chat/v1; HttpOnly; SameSite=Lax; Max-Age=7200; Secure"]);
    expect(res.headers.get("x-powered-by")).toBeNull();
    expect(res.headers.get("server")).toBeNull();
    expect(res.headers.get("x-request-id")).toBeNull();
    expect(res.headers.get("cache-control")).toBe("no-store");
  });

  it("passes a 4xx with its JSON error and Retry-After", async () => {
    const u = upstreamReturning(() => json({ error: { code: "RESOURCE_LIMIT" } }, 429, { "retry-after": "30" }));
    const res = await createAssistantProxy({ upstream: UP, fetchImpl: u.fetchImpl })(req("POST", "/messages", { body: "{}", headers: { "content-type": "application/json" } }), ["messages"]);
    expect(res.status).toBe(429);
    expect(res.headers.get("retry-after")).toBe("30");
    expect(((await res.json()) as { error: { code: string } }).error.code).toBe("RESOURCE_LIMIT");
  });

  it("relays /out as a redirect without following it", async () => {
    const u = upstreamReturning(() => new Response(null, { status: 302, headers: { location: "https://www.example.com/docs" } }));
    const res = await createAssistantProxy({ upstream: UP, fetchImpl: u.fetchImpl })(req("GET", "/out/DEV-DOCS"), ["out", "DEV-DOCS"]);
    expect(res.status).toBe(302);
    expect(res.headers.get("location")).toBe("https://www.example.com/docs");
    expect(sent(u.f).init.redirect).toBe("manual");
    expect(sent(u.f).url).toBe(`${UP}/api/public/chat/v1/out/DEV-DOCS`);
  });

  it("refuses a redirect that is not a web URL", async () => {
    const u = upstreamReturning(() => new Response(null, { status: 302, headers: { location: "javascript:alert(1)" } }));
    const res = await createAssistantProxy({ upstream: UP, fetchImpl: u.fetchImpl })(req("GET", "/out/X"), ["out", "X"]);
    expect(res.status).toBe(503);
    expect(res.headers.get("location")).toBeNull();
  });
});

describe("a failing assistant is never turned into a success", () => {
  it("answers 503 UNAVAILABLE when the upstream is down", async () => {
    const fetchImpl = (async () => {
      throw new TypeError("connect ECONNREFUSED");
    }) as unknown as typeof fetch;
    const res = await createAssistantProxy({ upstream: UP, fetchImpl })(req("GET", "/bootstrap"), ["bootstrap"]);
    expect(res.status).toBe(503);
    const body = (await res.json()) as { error: { code: string } };
    expect(body.error.code).toBe("UNAVAILABLE");
    expect(JSON.stringify(body)).not.toMatch(/ECONNREFUSED|localhost|8210/);
  });

  it("answers 503 when the upstream times out (3 s for bootstrap, 15 s otherwise)", async () => {
    const seen: number[] = [];
    const spy = vi.spyOn(AbortSignal, "timeout").mockImplementation((ms: number) => {
      seen.push(ms);
      return new AbortController().signal;
    });
    const hang = (async (_u: RequestInfo | URL, i?: RequestInit) => {
      void i;
      throw new DOMException("timed out", "TimeoutError");
    }) as unknown as typeof fetch;
    const proxy = createAssistantProxy({ upstream: UP, fetchImpl: hang });
    expect((await proxy(req("GET", "/bootstrap"), ["bootstrap"])).status).toBe(503);
    expect((await proxy(req("POST", "/messages", { body: "{}", headers: { "content-type": "application/json" } }), ["messages"])).status).toBe(503);
    expect(seen).toEqual([3000, 15000]);
    spy.mockRestore();
  });

  it.each([500, 502, 504])("turns an upstream %i into 503 UNAVAILABLE and hides its body", async (status) => {
    const u = upstreamReturning(() => new Response("Traceback: secret path /srv/app.py", { status, headers: { "content-type": "text/plain" } }));
    const res = await createAssistantProxy({ upstream: UP, fetchImpl: u.fetchImpl })(req("GET", "/bootstrap"), ["bootstrap"]);
    expect(res.status).toBe(503);
    expect(await res.text()).not.toContain("Traceback");
  });

  it("does not relay a non-JSON 200 (an HTML gateway page is not an answer)", async () => {
    const u = upstreamReturning(() => new Response("<html>welcome</html>", { status: 200, headers: { "content-type": "text/html" } }));
    const res = await createAssistantProxy({ upstream: UP, fetchImpl: u.fetchImpl })(req("GET", "/bootstrap"), ["bootstrap"]);
    expect(res.status).toBe(503);
  });
});

describe("upstreamFromEnv", () => {
  it("defaults to the local assistant and only accepts http(s)", () => {
    expect(upstreamFromEnv(undefined)).toBe("http://localhost:8210");
    expect(upstreamFromEnv("https://assistant.internal:8443/x")).toBe("https://assistant.internal:8443");
    expect(upstreamFromEnv("file:///etc/passwd")).toBe("http://localhost:8210");
    expect(upstreamFromEnv("not a url")).toBe("http://localhost:8210");
  });
});
