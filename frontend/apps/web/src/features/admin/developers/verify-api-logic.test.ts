import { describe, expect, it } from "vitest";

import { DEFAULT_SCOPES, SCOPES, canIssue, credentialState, firstOriginProblem, nameProblem, originProblem, parseOrigins, scopeLabel, tokenSnippet, whoamiSnippet } from "./verify-api-logic";

describe("scopes in words", () => {
  it("names every scope the cell knows, once, with a plain explanation", () => {
    expect(SCOPES.map((s) => s.id).sort()).toEqual(
      [
        "applications:submit",
        "decisions:read",
        "products:read",
        "usage:read",
        "verifications:create",
        "verifications:list",
        "verifications:read",
        "verifications:recheck",
        "verifications:resume",
        "webhooks:read",
        "webhooks:write",
        "workflows:read",
      ].sort(),
    );
    for (const s of SCOPES) {
      expect(s.label.length).toBeGreaterThan(5);
      expect(s.help.endsWith(".")).toBe(true);
    }
    expect(scopeLabel("decisions:read")).toBe("Read the result");
    expect(scopeLabel("unknown:thing")).toBe("unknown:thing");
  });
  it("gives a new sandbox application everything but the call that asks for production", () => {
    expect(DEFAULT_SCOPES).toHaveLength(SCOPES.length - 1);
    expect(DEFAULT_SCOPES).not.toContain("applications:submit");
  });
});

describe("a name and origins", () => {
  it("asks for a real name", () => {
    expect(nameProblem("Checkout website")).toBeNull();
    expect(nameProblem("  Web  app  ")).toBeNull();
    for (const bad of ["", "ab", "-start", "end-", "has/slash", "x".repeat(81)]) expect(nameProblem(bad), bad).not.toBeNull();
  });
  it("reads one origin per line or comma, dropping blanks and repeats", () => {
    expect(parseOrigins(" https://a.example.com \n\nhttps://b.example.com, https://a.example.com")).toEqual(["https://a.example.com", "https://b.example.com"]);
    expect(parseOrigins("")).toEqual([]);
  });
  it("accepts exact https origins and a local http one, and says why anything else is refused", () => {
    for (const ok of ["https://app.example.com", "https://app.example.com/", "http://localhost:3000"]) expect(originProblem(ok), ok).toBeNull();
    expect(originProblem("http://example.com")).toMatch(/https/);
    expect(originProblem("https://*.example.com")).toMatch(/wildcard/);
    expect(originProblem("https://example.com/path")).toMatch(/no path/);
    expect(originProblem("https://user:pw@example.com")).toMatch(/user name/);
    expect(originProblem("not a url")).toMatch(/not a web address/);
    expect(firstOriginProblem(["https://ok.example.com", "http://bad.example.com"])).toMatch(/https/);
    expect(firstOriginProblem(Array.from({ length: 21 }, (_, i) => `https://h${i}.example.com`))).toMatch(/At most 20/);
    expect(firstOriginProblem([])).toBeNull();
  });
});

describe("credentials and the first call", () => {
  it("says in words which credentials still work", () => {
    expect(credentialState({ status: "ACTIVE" })).toEqual({ label: "Active", tone: "ok" });
    expect(credentialState({ status: "ROTATING" }).label).toBe("Being replaced");
    expect(credentialState({ status: "REVOKED" }).tone).toBe("danger");
    expect(credentialState({ status: "EXPIRED" }).tone).toBe("neutral");
  });
  it("lets only an active application have credentials issued", () => {
    expect(canIssue("ACTIVE")).toBe(true);
    expect(canIssue("SUSPENDED")).toBe(false);
    expect(canIssue("RETIRED")).toBe(false);
  });
  it("shows the first calls with a placeholder, never a real secret", () => {
    const t = tokenSnippet("https://api.example.com", "zpc_0123456789abcdef0123");
    expect(t).toContain("https://api.example.com/v1/verify-api/oauth2/token");
    expect(t).toContain('"client_id":"zpc_0123456789abcdef0123"');
    expect(t).toContain("<your secret>");
    expect(t).not.toMatch(/zps_/);
    expect(whoamiSnippet("https://api.example.com")).toContain("Authorization: Bearer <access_token");
  });
});
