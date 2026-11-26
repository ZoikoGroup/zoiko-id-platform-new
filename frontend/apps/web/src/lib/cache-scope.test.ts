import { describe, expect, it } from "vitest";

import { identityOf, isForeign, mustFlush } from "./cache-scope";

const a = { authenticated: true as const, subject: { sub: "p1", tenantId: "t-A" } };

describe("the console cache belongs to one person in one company", () => {
  it("names the identity and says nothing before a session has loaded or after sign-out", () => {
    expect(identityOf(a)).toBe("t-A|p1");
    expect(identityOf({ authenticated: false })).toBeNull();
    expect(identityOf(undefined)).toBeNull();
    expect(identityOf({ authenticated: true, subject: { sub: "p1" } })).toBe("-|p1");
  });

  it("flushes when company A becomes company B, or one person becomes another, and never on first load or on a refresh of the same identity", () => {
    const A = identityOf(a);
    const B = identityOf({ authenticated: true, subject: { sub: "p1", tenantId: "t-B" } });
    const other = identityOf({ authenticated: true, subject: { sub: "p2", tenantId: "t-A" } });
    expect(mustFlush(A, B)).toBe(true);
    expect(mustFlush(A, other)).toBe(true);
    expect(mustFlush(A, null)).toBe(true); // signed out: nothing of the old identity may stay
    expect(mustFlush(null, A)).toBe(false); // first load
    expect(mustFlush(A, A)).toBe(false); // a refresh of the same session
    expect(mustFlush(null, null)).toBe(false);
  });

  it("keeps the session query and drops usage, keys, applications and sessions", () => {
    expect(isForeign(["session"])).toBe(false);
    for (const key of [["developers", "applications"], ["admin", "evidence", "keys"], ["tenant", "permissions", "rights"], ["admin", "routing", "capabilities"], ["usage"]]) {
      expect(isForeign(key)).toBe(true);
    }
  });
});
