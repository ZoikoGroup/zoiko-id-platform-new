import { describe, expect, it } from "vitest";

import { parseJwks } from "./jwks";

const pub = { kty: "EC", crv: "P-256", kid: "scim-1", x: "x", y: "y" };

describe("parseJwks", () => {
  it("accepts a JWK Set of public keys", () => {
    expect(parseJwks(JSON.stringify({ keys: [pub] })).jwks).toEqual({ keys: [pub] });
  });

  it.each([
    ["not json", "{nope", /not valid JSON/],
    ["an array", "[]", /JWK Set/],
    ["no keys member", "{}", /JWK Set/],
    ["an empty key list", '{"keys": []}', /JWK Set/],
    ["more than five keys", JSON.stringify({ keys: Array.from({ length: 6 }, () => pub) }), /1 to 5/],
    ["an unknown key type", JSON.stringify({ keys: [{ ...pub, kty: "oct" }] }), /kty/],
    ["a key without a kid", JSON.stringify({ keys: [{ kty: "EC", x: "x", y: "y" }] }), /kid/],
  ])("refuses %s", (_what, text, message) => {
    const r = parseJwks(text);
    expect(r.jwks).toBeUndefined();
    expect(r.error).toMatch(message);
  });

  it.each(["d", "p", "q", "dp", "dq", "qi", "k", "oth"])("refuses a private member %s", (member) => {
    const r = parseJwks(JSON.stringify({ keys: [{ ...pub, [member]: "secret" }] }));
    expect(r.jwks).toBeUndefined();
    expect(r.error).toMatch(/private key/);
  });
});
