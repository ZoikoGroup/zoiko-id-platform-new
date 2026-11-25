import { describe, expect, it } from "vitest";

import { addressProblem, allowlistProblem, assertionSnippet, parseAllowlist, parsePublicKey } from "./credential-options-logic";

describe("the IP allowlist form", () => {
  it("reads one entry per line or comma, without blanks or repeats", () => {
    expect(parseAllowlist(" 203.0.113.7 \n\n203.0.113.7, 10.0.0.0/8 ")).toEqual(["203.0.113.7", "10.0.0.0/8"]);
    expect(parseAllowlist("")).toEqual([]);
  });
  it("accepts addresses and networks and names what is wrong with the rest", () => {
    for (const ok of ["203.0.113.7", "203.0.113.0/24", "2001:db8::/48", "::1"]) expect(addressProblem(ok)).toBeNull();
    expect(addressProblem("0.0.0.0/0")).toContain("every address");
    expect(addressProblem("::/0")).toContain("every address");
    expect(addressProblem("203.0.113.300")).toContain("not an IP");
    expect(addressProblem("10.0.0.0/33")).toContain("over 32");
    expect(addressProblem("2001:db8::/129")).toContain("over 128");
    expect(addressProblem("example.com")).toContain("not an IP");
  });
  it("limits the number of entries", () => {
    const many = Array.from({ length: 21 }, (_, i) => `10.0.${i}.1`);
    expect(allowlistProblem(many)).toContain("At most 20");
    expect(allowlistProblem(many.slice(0, 20))).toBeNull();
    expect(allowlistProblem([])).toBeNull();
  });
});

describe("pasting a public key", () => {
  const ed = { kty: "OKP", crv: "Ed25519", x: "abc" };
  it("does nothing for an empty box", () => {
    expect(parsePublicKey("  ")).toEqual({ jwk: null, problem: null });
  });
  it("accepts an Ed25519 or an EC P-256 public key", () => {
    expect(parsePublicKey(JSON.stringify(ed)).jwk).toEqual(ed);
    const ec = { kty: "EC", crv: "P-256", x: "a", y: "b", kid: "k1" };
    expect(parsePublicKey(JSON.stringify(ec)).jwk).toEqual(ec);
  });
  it("refuses a key with private material before it is ever sent", () => {
    const r = parsePublicKey(JSON.stringify({ ...ed, d: "secret" }));
    expect(r.jwk).toBeNull();
    expect(r.problem).toContain("private key material");
    expect(r.problem).toContain("exposed");
  });
  it("names what is wrong with anything else", () => {
    expect(parsePublicKey("not json").problem).toContain("valid JSON");
    expect(parsePublicKey("[1]").problem).toContain("one JSON object");
    expect(parsePublicKey(JSON.stringify({ kty: "RSA", n: "x", e: "AQAB" })).problem).toContain("EC P-256 or an Ed25519");
    expect(parsePublicKey(JSON.stringify({ kty: "EC", crv: "P-256", x: "a" })).problem).toContain("EC P-256 or an Ed25519");
    expect(parsePublicKey(JSON.stringify({ ...ed, x: 5 })).problem).toContain("text");
  });
});

describe("the assertion snippet", () => {
  it("names the client and the audience and carries no key", () => {
    const s = assertionSnippet("https://api.example.com", "zpc_abc");
    expect(s).toContain("iss = sub = zpc_abc");
    expect(s).toContain("aud = https://api.example.com/v1/verify-api/oauth2/token");
    expect(s).toContain("client_assertion_type=urn:ietf:params:oauth:client-assertion-type:jwt-bearer");
    expect(s).not.toMatch(/BEGIN .*PRIVATE/);
  });
});
