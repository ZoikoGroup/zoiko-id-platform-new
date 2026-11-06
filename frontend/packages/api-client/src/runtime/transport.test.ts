import { describe, expect, it, vi } from "vitest";

import { routeStepUp, ApiError } from "./errors";
import { createTransport } from "./transport";

const refusal = (details: { field: string; issue: string }[]) =>
  new Response(JSON.stringify({ success: false, error: { code: "STEP_UP_REQUIRED", message: "A fresh strong authentication is required.", details } }), {
    status: 403,
    headers: { "content-type": "application/json" },
  });
const bound = [
  { field: "tier", issue: "S2" },
  { field: "action", issue: "create-expand-high-privilege-ai-agent-mandate" },
  { field: "binding", issue: "route" },
  { field: "resource", issue: "tenant:t-1" },
  { field: "change", issue: JSON.stringify({ operation: "x", body_sha256: "abc" }) },
];
const ok = () => new Response(JSON.stringify({ done: true }), { status: 200, headers: { "content-type": "application/json" } });

describe("route step-up retry", () => {
  it("reads the change the server derived, and nothing from another kind of step-up", async () => {
    const e = await ApiError.from(refusal(bound));
    expect(routeStepUp(e)).toEqual({ action: "create-expand-high-privilege-ai-agent-mandate", resource: "tenant:t-1", change: { operation: "x", body_sha256: "abc" } });
    expect(routeStepUp(await ApiError.from(refusal([{ field: "tier", issue: "S2" }])))).toBeNull();
    expect(routeStepUp(await ApiError.from(refusal([...bound.slice(0, 4), { field: "change", issue: "not json" }])))).toBeNull();
    expect(routeStepUp(new Error("x"))).toBeNull();
  });

  it("retries the same request once with the proof made for it", async () => {
    const fetchImpl = vi.fn().mockResolvedValueOnce(refusal(bound)).mockResolvedValueOnce(ok());
    const prove = vi.fn().mockResolvedValue("proof-1");
    const t = createTransport({ baseUrl: "/api", fetchImpl, onRouteStepUp: prove });
    await expect(t.request("POST", "/v1/mandates", { body: { a: 1 } })).resolves.toEqual({ done: true });
    expect(prove).toHaveBeenCalledWith({ action: "create-expand-high-privilege-ai-agent-mandate", resource: "tenant:t-1", change: { operation: "x", body_sha256: "abc" } });
    expect(fetchImpl).toHaveBeenCalledTimes(2);
    const [first, second] = fetchImpl.mock.calls.map((c) => c[1] as RequestInit);
    expect(second!.body).toBe(first!.body); // the same bytes: the proof is bound to a digest of them
    expect((second!.headers as Record<string, string>)["x-step-up-proof"]).toBe("proof-1");
    expect((first!.headers as Record<string, string>)["x-step-up-proof"]).toBeUndefined();
  });

  it("gives up with the original refusal when the person declines, and never retries twice", async () => {
    const fetchImpl = vi.fn().mockResolvedValue(refusal(bound));
    const t = createTransport({ baseUrl: "/api", fetchImpl, onRouteStepUp: vi.fn().mockResolvedValue(undefined) });
    await expect(t.request("POST", "/v1/mandates", { body: {} })).rejects.toMatchObject({ code: "STEP_UP_REQUIRED" });
    expect(fetchImpl).toHaveBeenCalledTimes(1);
    const again = vi.fn().mockResolvedValue(refusal(bound));
    const t2 = createTransport({ baseUrl: "/api", fetchImpl: again, onRouteStepUp: vi.fn().mockResolvedValue("p") });
    await expect(t2.request("POST", "/v1/mandates", { body: {} })).rejects.toMatchObject({ code: "STEP_UP_REQUIRED" });
    expect(again).toHaveBeenCalledTimes(2);
  });

  it("does not retry another refusal, or a refusal without a handler", async () => {
    const fetchImpl = vi.fn().mockResolvedValue(new Response(JSON.stringify({ success: false, error: { code: "FORBIDDEN", message: "no" } }), { status: 403 }));
    const prove = vi.fn();
    await expect(createTransport({ baseUrl: "/api", fetchImpl, onRouteStepUp: prove }).request("POST", "/x", {})).rejects.toMatchObject({ code: "FORBIDDEN" });
    expect(prove).not.toHaveBeenCalled();
    const plain = vi.fn().mockResolvedValue(refusal(bound));
    await expect(createTransport({ baseUrl: "/api", fetchImpl: plain }).request("POST", "/x", {})).rejects.toMatchObject({ code: "STEP_UP_REQUIRED" });
    expect(plain).toHaveBeenCalledTimes(1);
  });
});

describe("the CSRF token on writes", () => {
  it("waits for a token that is still loading instead of sending the write without one", async () => {
    const fetchImpl = vi.fn().mockResolvedValue(ok());
    let release!: (token: string) => void;
    const loading = new Promise<string>((resolve) => (release = resolve));
    const t = createTransport({ baseUrl: "/api", fetchImpl, csrf: () => loading });
    const pending = t.request("POST", "/v1/x", { body: {} });
    await Promise.resolve();
    expect(fetchImpl).not.toHaveBeenCalled(); // nothing goes out until the token is known
    release("tok-1");
    await pending;
    expect(((fetchImpl.mock.calls[0]![1] as RequestInit).headers as Record<string, string>)["x-zid-csrf"]).toBe("tok-1");
  });

  it("sends a token it already has, and none on a read", async () => {
    const fetchImpl = vi.fn().mockImplementation(async () => ok());
    const t = createTransport({ baseUrl: "/api", fetchImpl, csrf: () => "tok-2" });
    await t.request("PATCH", "/v1/x", { body: {} });
    await t.request("GET", "/v1/x");
    const [write, read] = fetchImpl.mock.calls.map((c) => (c[1] as RequestInit).headers as Record<string, string>);
    expect(write!["x-zid-csrf"]).toBe("tok-2");
    expect(read!["x-zid-csrf"]).toBeUndefined();
  });
});
