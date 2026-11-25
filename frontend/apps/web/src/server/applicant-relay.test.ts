import { afterEach, describe, expect, it, vi } from "vitest";

import { allowed, query, relay, TOKEN_HEADER } from "./applicant-relay";

const REF = "vs_0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0";

afterEach(() => vi.unstubAllGlobals());

describe("the hosted verification relay (WF-01 §08)", () => {
  it("passes only the person's own session endpoints", () => {
    expect(allowed("POST", "launch")).toBe(true);
    expect(allowed("GET", `${REF}/public-context`)).toBe(true);
    expect(allowed("POST", `${REF}/evidence/document-image`)).toBe(true);
    expect(allowed("GET", `${REF}/decision`)).toBe(false); // the business's result is never reachable from the person's browser
    expect(allowed("POST", `${REF}/resume-token`)).toBe(false);
    expect(allowed("POST", `${REF}/../../v1/idv/cases`)).toBe(false);
    expect(allowed("GET", "launch")).toBe(false);
  });

  it("forwards only the two device flags", () => {
    expect(query(new URLSearchParams("nfc=true&camera=false&debug=1"))).toBe("?nfc=true&camera=false");
    expect(query(new URLSearchParams("nfc=yes"))).toBe("");
  });

  it("forwards the session credential and nothing else; a refused path never reaches the cell", async () => {
    const fetch = vi.fn().mockResolvedValue(new Response('{"state":"CREATED"}', { status: 200, headers: { "content-type": "application/json" } }));
    vi.stubGlobal("fetch", fetch);
    const req = new Request(`https://web.example/api/public/verify-session/${REF}/public-status`, {
      headers: { [TOKEN_HEADER]: "t", cookie: "zid_session=secret", authorization: "Bearer x" },
    });
    const res = await relay(req, `${REF}/public-status`, "https://cell.example/");
    expect(res.status).toBe(200);
    const [url, init] = fetch.mock.calls[0] as [string, RequestInit];
    expect(url).toBe(`https://cell.example/v2/verification-sessions/${REF}/public-status`);
    expect(init.headers).toEqual({ accept: "application/json", [TOKEN_HEADER]: "t" });

    fetch.mockClear();
    const refused = await relay(new Request("https://web.example/x", { method: "POST" }), `${REF}/decision`, "https://cell.example");
    expect(refused.status).toBe(404);
    expect(fetch).not.toHaveBeenCalled();
  });
});
