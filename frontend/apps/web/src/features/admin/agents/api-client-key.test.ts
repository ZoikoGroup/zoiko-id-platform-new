/**
 * CR-141: a customer's public key reaches the registration form in whatever shape they hold it, and a private key never does.
 * The keys here are real ones made by the platform's own WebCrypto, so the conversions are checked for real, not against canned text.
 */
import { createHash, webcrypto } from "node:crypto";
import { beforeAll, describe, expect, it, vi } from "vitest";

import { generateClientKeyPair, normalizePublicKey, thumbprint } from "./api-client-key";

beforeAll(() => {
  vi.stubGlobal("crypto", webcrypto);
});

const wrap = (label: string, der: ArrayBuffer) => `-----BEGIN ${label}-----\n${Buffer.from(der).toString("base64").match(/.{1,64}/g)!.join("\n")}\n-----END ${label}-----\n`;

async function publicPem(algorithm: EcKeyGenParams | RsaHashedKeyGenParams | { name: "Ed25519" }, usages: KeyUsage[] = ["sign", "verify"]) {
  const pair = (await webcrypto.subtle.generateKey(algorithm as never, true, usages)) as CryptoKeyPair;
  return {
    pair,
    pem: wrap("PUBLIC KEY", await webcrypto.subtle.exportKey("spki", pair.publicKey)),
    privatePem: wrap("PRIVATE KEY", await webcrypto.subtle.exportKey("pkcs8", pair.privateKey)),
  };
}

const rfc7638 = (members: Record<string, string>) => createHash("sha256").update(JSON.stringify(members)).digest("base64url");

describe("a PEM public key (what openssl -pubout writes)", () => {
  it("becomes a key set with the RFC 7638 fingerprint as its kid, for P-256", async () => {
    const { pem } = await publicPem({ name: "ECDSA", namedCurve: "P-256" });
    const r = await normalizePublicKey(pem);
    expect(r.error).toBeUndefined();
    const k = r.jwks!.keys[0]!;
    expect(k).toMatchObject({ kty: "EC", crv: "P-256" });
    expect(k.kid).toBe(rfc7638({ crv: k.crv!, kty: k.kty!, x: k.x!, y: k.y! })); // an independent computation agrees
    expect(r.note).toMatch(/Converted from a PEM/);
  });
  it.each([
    ["P-384", { name: "ECDSA", namedCurve: "P-384" } as EcKeyGenParams],
    ["Ed25519", { name: "Ed25519" } as never],
    ["RSA 2048", { name: "RSASSA-PKCS1-v1_5", modulusLength: 2048, publicExponent: new Uint8Array([1, 0, 1]), hash: "SHA-256" } as RsaHashedKeyGenParams],
  ])("is read for %s", async (_label, algorithm) => {
    const { pem } = await publicPem(algorithm);
    const r = await normalizePublicKey(pem);
    expect(r.error).toBeUndefined();
    expect(r.jwks!.keys[0]!.kid).toBeTruthy();
  });
  it("refuses an RSA key under 2048 bits", async () => {
    const { pem } = await publicPem({ name: "RSASSA-PKCS1-v1_5", modulusLength: 1024, publicExponent: new Uint8Array([1, 0, 1]), hash: "SHA-256" } as RsaHashedKeyGenParams);
    const r = await normalizePublicKey(pem);
    expect(r.jwks).toBeUndefined();
    expect(r.error).toMatch(/2048|could not read/);
  });
});

describe("a private key is refused, wherever it is hiding, and is never repeated", () => {
  it("as a PEM private key", async () => {
    const { privatePem } = await publicPem({ name: "ECDSA", namedCurve: "P-256" });
    const r = await normalizePublicKey(privatePem);
    expect(r.jwks).toBeUndefined();
    expect(r.error).toMatch(/private material/);
    expect(r.error).not.toContain(privatePem.split("\n")[1]!);
  });
  it("as the older RSA and EC PEM labels", async () => {
    for (const label of ["RSA PRIVATE KEY", "EC PRIVATE KEY"]) {
      const r = await normalizePublicKey(`-----BEGIN ${label}-----\nMIIBSECRETSECRET\n-----END ${label}-----`);
      expect(r.error).toMatch(/private material/);
      expect(r.error).not.toMatch(/SECRET/);
    }
  });
  it("as a private member in a JWK, alone or in a key set", async () => {
    const { pair } = await publicPem({ name: "ECDSA", namedCurve: "P-256" });
    const full = (await webcrypto.subtle.exportKey("jwk", pair.privateKey)) as Record<string, string>;
    for (const raw of [JSON.stringify(full), JSON.stringify({ keys: [full] })]) {
      const r = await normalizePublicKey(raw);
      expect(r.jwks).toBeUndefined();
      expect(r.error).toMatch(/private material/);
      expect(r.error).not.toContain(full.d!);
    }
  });
});

describe("a key set or a single key", () => {
  const base = async () => {
    const { pair } = await publicPem({ name: "ECDSA", namedCurve: "P-256" });
    const j = (await webcrypto.subtle.exportKey("jwk", pair.publicKey)) as Record<string, string>;
    return { kty: j.kty!, crv: j.crv!, x: j.x!, y: j.y! };
  };
  it("a single key is wrapped and named", async () => {
    const r = await normalizePublicKey(JSON.stringify(await base()));
    expect(r.jwks!.keys).toHaveLength(1);
    expect(r.jwks!.keys[0]!.kid).toBeTruthy();
    expect(r.note).toMatch(/Wrapped/);
  });
  it("keeps a kid the customer chose, and drops members we do not use", async () => {
    const r = await normalizePublicKey(JSON.stringify({ keys: [{ ...(await base()), kid: "acme-prod-2026-11", key_ops: ["verify"], ext: "true" }] }));
    expect(r.jwks!.keys[0]).toMatchObject({ kid: "acme-prod-2026-11" });
    expect(Object.keys(r.jwks!.keys[0]!)).not.toContain("key_ops");
  });
  it("refuses a remote key address, a certificate chain, an encryption key and a shared secret", async () => {
    const b = await base();
    for (const bad of [{ ...b, jku: "https://x" }, { ...b, x5c: ["AAAA"] }, { ...b, use: "enc" }, { ...b, alg: "HS256" }]) {
      const r = await normalizePublicKey(JSON.stringify({ keys: [{ ...bad, kid: "k" }] }));
      expect(r.jwks).toBeUndefined();
      expect(r.error).toBeTruthy();
    }
  });
  it("refuses a curve we do not allow, an unknown key type, an empty set, too many keys and a repeated kid", async () => {
    const b = await base();
    expect((await normalizePublicKey(JSON.stringify({ keys: [{ ...b, crv: "secp256k1", kid: "k" }] }))).error).toMatch(/P-256 or P-384/);
    expect((await normalizePublicKey(JSON.stringify({ keys: [{ kty: "oct", kid: "k" }] }))).error).toMatch(/elliptic-curve/);
    expect((await normalizePublicKey(JSON.stringify({ keys: [] }))).error).toMatch(/1 to 5/);
    expect((await normalizePublicKey(JSON.stringify({ keys: Array(6).fill({ ...b, kid: "k" }) }))).error).toMatch(/1 to 5/);
    expect((await normalizePublicKey(JSON.stringify({ keys: [{ ...b, kid: "same" }, { ...b, kid: "same" }] }))).error).toMatch(/same kid/);
  });
  it("explains a certificate, a PEM that is not a public key, empty input and text that is not a key", async () => {
    expect((await normalizePublicKey("-----BEGIN CERTIFICATE-----\nAAAA\n-----END CERTIFICATE-----")).error).toMatch(/certificate/);
    expect((await normalizePublicKey("-----BEGIN PUBLIC KEY-----\n!!!\n-----END PUBLIC KEY-----")).error).toMatch(/not a public key/);
    expect((await normalizePublicKey("   ")).error).toMatch(/Paste the public key/);
    expect((await normalizePublicKey("hello")).error).toMatch(/not a key we can read/);
  });
});

describe("the fingerprint is the one the server computes", () => {
  it("matches RFC 7638 for EC, OKP and RSA members", async () => {
    expect(await thumbprint({ kty: "EC", crv: "P-256", x: "xx", y: "yy" })).toBe(rfc7638({ crv: "P-256", kty: "EC", x: "xx", y: "yy" }));
    expect(await thumbprint({ kty: "OKP", crv: "Ed25519", x: "xx" })).toBe(rfc7638({ crv: "Ed25519", kty: "OKP", x: "xx" }));
    expect(await thumbprint({ kty: "RSA", n: "nn", e: "AQAB" })).toBe(rfc7638({ e: "AQAB", kty: "RSA", n: "nn" }));
  });
});

describe("making a key pair in the browser", () => {
  it("returns a public key set with no private member, and a private key that signs what the public key verifies", async () => {
    const k = await generateClientKeyPair();
    expect(k.jwks.keys).toHaveLength(1);
    const pub = k.jwks.keys[0]!;
    expect(Object.keys(pub).some((m) => ["d", "p", "q", "k"].includes(m))).toBe(false);
    expect(pub.kid).toBe(k.kid);
    expect(k.privatePem).toMatch(/^-----BEGIN PRIVATE KEY-----\n/);

    // the pair is a real pair: sign with the private PEM, verify with the registered public key
    const der = Uint8Array.from(atob(k.privatePem.replace(/-----[A-Z ]+-----|\s/g, "")), (c) => c.charCodeAt(0));
    const priv = await webcrypto.subtle.importKey("pkcs8", der, { name: "ECDSA", namedCurve: "P-256" }, false, ["sign"]);
    const verifyKey = await webcrypto.subtle.importKey("jwk", { kty: pub.kty, crv: pub.crv, x: pub.x, y: pub.y }, { name: "ECDSA", namedCurve: "P-256" }, false, ["verify"]);
    const msg = new TextEncoder().encode("assertion");
    const sig = await webcrypto.subtle.sign({ name: "ECDSA", hash: "SHA-256" }, priv, msg);
    expect(await webcrypto.subtle.verify({ name: "ECDSA", hash: "SHA-256" }, verifyKey, sig, msg)).toBe(true);

    // and what the form would register is exactly accepted, with nothing added
    const again = await normalizePublicKey(JSON.stringify(k.jwks));
    expect(again.error).toBeUndefined();
    expect(again.jwks).toEqual(k.jwks);
  });
  it("never puts the private key into the public text", async () => {
    const k = await generateClientKeyPair();
    const body = k.privatePem.replace(/-----[A-Z ]+-----|\s/g, "");
    expect(JSON.stringify(k.jwks)).not.toContain(body.slice(20, 60));
  });
});
