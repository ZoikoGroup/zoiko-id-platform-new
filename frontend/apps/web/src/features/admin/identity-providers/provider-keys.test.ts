import { describe, expect, it } from "vitest";

import { parseProviderKeys } from "./provider-keys";

const PUBLIC = { kty: "EC", crv: "P-256", kid: "k1", x: "AQAB", y: "AQAB" };

describe("the provider's public keys", () => {
  it("accepts a key list as the provider publishes it", () => {
    expect(parseProviderKeys(JSON.stringify({ keys: [PUBLIC] })).keys).toEqual({ keys: [PUBLIC] });
  });

  it("wraps a single key or a bare list into a key list", () => {
    expect(parseProviderKeys(JSON.stringify(PUBLIC)).keys).toEqual({ keys: [PUBLIC] });
    expect(parseProviderKeys(JSON.stringify([PUBLIC])).keys).toEqual({ keys: [PUBLIC] });
  });

  it("refuses a private key and says why", () => {
    const r = parseProviderKeys(JSON.stringify({ ...PUBLIC, d: "secret" }));
    expect(r.keys).toBeUndefined();
    expect(r.error).toMatch(/private key/i);
    expect(parseProviderKeys(JSON.stringify({ keys: [{ ...PUBLIC, d: "secret" }] })).error).toMatch(/private key/i);
  });

  it("refuses text that is not a key list", () => {
    expect(parseProviderKeys("not json").error).toMatch(/not valid JSON/);
    expect(parseProviderKeys("{}").error).toMatch(/key list/);
    expect(parseProviderKeys('{"keys": []}').error).toMatch(/key list/);
    expect(parseProviderKeys('{"keys": [{"kid": "x"}]}').error).toMatch(/kty/);
  });
});

describe("the key list as a person reads it", () => {
  it("shows each key by its id, type, algorithm and use, never its key material", async () => {
    const { summarizeKeys } = await import("./provider-keys");
    const text = JSON.stringify({ keys: [{ kty: "RSA", kid: "YV485m885mcWGuwgli05h", alg: "RS256", use: "sig", n: "abc", e: "AQAB" }, { kty: "EC", kid: "O_JxEAA0ROBsCQNZy476j" }] });
    expect(summarizeKeys(text)).toEqual([
      { kid: "YV485m885mcWGuwgli05h", kty: "RSA", alg: "RS256", use: "sig" },
      { kid: "O_JxEAA0ROBsCQNZy476j", kty: "EC", alg: "", use: "" },
    ]);
    expect(JSON.stringify(summarizeKeys(text))).not.toContain("abc");
  });
  it("shows nothing for text that is not a key list", async () => {
    const { summarizeKeys } = await import("./provider-keys");
    expect(summarizeKeys("")).toEqual([]);
    expect(summarizeKeys("not json")).toEqual([]);
  });
});
