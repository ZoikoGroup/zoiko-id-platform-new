/** The browser-made key pair: the public half is what Zoiko iD registers, the private half is only ever shown to the person. */
import { describe, expect, it } from "vitest";

import { parseJwks } from "@/features/admin/provisioning/jwks";

import { generateKeyPair } from "./keypair";

describe("generateKeyPair", () => {
  it("makes an ES256 pair whose public half the cell would accept and whose private half stays separate", async () => {
    const { publicKeys, privateKey } = await generateKeyPair("scim");
    const parsed = parseJwks(publicKeys);
    expect(parsed.error).toBeUndefined(); // no private member, a kid, a known key type
    const pub = (JSON.parse(publicKeys) as { keys: Record<string, string>[] }).keys[0]!;
    const priv = JSON.parse(privateKey) as Record<string, string>;
    expect(pub).toMatchObject({ kty: "EC", crv: "P-256", alg: "ES256", use: "sig" });
    expect(pub.kid).toMatch(/^scim-[0-9a-f]{8}$/);
    expect(priv.kid).toBe(pub.kid);
    expect(priv.d).toBeTruthy();
    expect("d" in pub).toBe(false);
    expect(publicKeys).not.toContain(priv.d!);
  });

  it("makes a different key every time", async () => {
    const a = await generateKeyPair();
    const b = await generateKeyPair();
    expect(a.privateKey).not.toBe(b.privateKey);
  });
});
