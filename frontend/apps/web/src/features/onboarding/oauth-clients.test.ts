import { describe, expect, it } from "vitest";

import { lines, parseKeys } from "./oauth-clients";

describe("OAuth client form input", () => {
  it("reads one redirect URL per line and ignores blanks", () => {
    expect(lines("https://a.example/cb\n\n  https://b.example/cb  \r\n")).toEqual(["https://a.example/cb", "https://b.example/cb"]);
  });

  it("accepts a JWK Set or a single JWK", () => {
    const key = { kty: "EC", kid: "k1" };
    expect(parseKeys(JSON.stringify({ keys: [key] }))).toEqual({ keys: [key] });
    expect(parseKeys(JSON.stringify(key))).toEqual({ keys: [key] });
  });

  it("explains an unreadable key instead of sending it", () => {
    expect(parseKeys("not json")).toMatch(/not a JSON key/);
    expect(parseKeys("[1,2]")).toMatch(/not a JSON key/);
  });
});
