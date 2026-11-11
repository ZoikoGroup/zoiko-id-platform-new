import { describe, expect, it } from "vitest";

import { CredentialsClient, CredentialsError, DECISION_TYP, RoomsPack, ScopeError, ZoikorumPack, type FetchLike, type Json } from "../src/index";

const BASE = "https://id.example";

const b64 = (b: Uint8Array | string): string =>
  btoa(typeof b === "string" ? b : String.fromCharCode(...b))
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/, "");

type Alg = "ES256" | "Ed25519";

async function makeKeys(alg: Alg = "ES256", kid = "k1") {
  const algo = alg === "ES256" ? { name: "ECDSA", namedCurve: "P-256" } : { name: "Ed25519" };
  const pair = (await crypto.subtle.generateKey(algo, true, ["sign", "verify"])) as CryptoKeyPair;
  const pub = (await crypto.subtle.exportKey("jwk", pair.publicKey)) as Json;
  const jwk = { ...pub, kid, alg, use: "sig" };
  return {
    jwks: { keys: [jwk] },
    async sign(claims: Json, o: { typ?: string; kid?: string; alg?: string } = {}): Promise<string> {
      const header = { alg: o.alg ?? alg, kid: o.kid ?? kid, typ: o.typ ?? DECISION_TYP };
      const signing = `${b64(JSON.stringify(header))}.${b64(JSON.stringify(claims))}`;
      if (header.alg === "none") return `${signing}.`;
      const data = new TextEncoder().encode(signing);
      const sig = new Uint8Array(await crypto.subtle.sign(alg === "ES256" ? { name: "ECDSA", hash: "SHA-256" } : { name: "Ed25519" }, pair.privateKey, data));
      return `${signing}.${b64(sig)}`;
    },
  };
}

const claims = (over: Json = {}): Json => ({
  consumer: "zoikorum",
  decision_id: "dec-1",
  engagement: "READY",
  tier: "A",
  iat: 1,
  dimensions: { IDENTITY: "VERIFIED", CREDENTIAL: "VERIFIED" },
  reason_codes: [],
  ...over,
});

type Call = { method: string; url: string; headers: Record<string, string>; body: Json | null };
function fake(...answers: Array<[number, unknown]>): { fetcher: FetchLike; calls: Call[] } {
  const calls: Call[] = [];
  const queue = [...answers];
  const fetcher: FetchLike = async (url, init) => {
    calls.push({ method: init.method, url, headers: init.headers, body: init.body ? (JSON.parse(init.body) as Json) : null });
    const [status, doc] = queue.shift()!;
    return { status, text: async () => (typeof doc === "string" ? doc : JSON.stringify(doc)) };
  };
  return { fetcher, calls };
}
const ok = (data: unknown): [number, unknown] => [201, { success: true, data }];
const err = (code: string, status = 422, extra: Json = {}): [number, unknown] => [status, { success: false, error: { code, message: code.toLowerCase(), ...extra } }];
const client = (...a: Array<[number, unknown]>) => {
  const f = fake(...a);
  return { c: new CredentialsClient(BASE, "tok-1", f.fetcher), calls: f.calls };
};

describe("client", () => {
  it("posts a check to its route with the bearer token and unwraps the envelope", async () => {
    const { c, calls } = client(ok({ check_id: "c1", decision: "VERIFIED" }));
    expect(await c.check("license", { license_ref: "L-1" }, { country: "US", subdivision: "CA" }, { profession_code: "LAW" })).toEqual({ check_id: "c1", decision: "VERIFIED" });
    expect(calls[0]).toMatchObject({ method: "POST", url: `${BASE}/credentials/professional-licenses/checks` });
    expect(calls[0]!.headers.authorization).toBe("Bearer tok-1");
    expect(calls[0]!.body).toEqual({ claim: { license_ref: "L-1" }, jurisdiction: { country: "US", subdivision: "CA" }, profession_code: "LAW" });
  });

  it("routes every claim kind and never sends an unknown one", async () => {
    const kinds = { academic: "academic", license: "professional-licenses", certification: "certifications", employment: "experience", cpd: "continuing-education", insurance: "insurance" } as const;
    const { c, calls } = client(...Object.keys(kinds).map(() => ok({})));
    for (const k of Object.keys(kinds) as Array<keyof typeof kinds>) await c.check(k, { x: "y" }, { country: "US" });
    expect(calls.map((x) => x.url)).toEqual(Object.values(kinds).map((r) => `${BASE}/credentials/${r}/checks`));
    expect(() => c.check("astrology" as never, {}, { country: "US" })).toThrow(ScopeError);
    expect(calls).toHaveLength(6);
  });

  it("turns an error into a CredentialsError carrying the platform's code", async () => {
    const { c } = client(err("PACK_UNAVAILABLE"), err("DEPENDENCY_UNAVAILABLE", 503, { retryable: true }), [502, "<html>bad gateway</html>"]);
    await expect(c.check("academic", { a: "b" }, { country: "FR" })).rejects.toMatchObject({ code: "PACK_UNAVAILABLE", status: 422, retryable: false });
    await expect(c.assertion("c1")).rejects.toMatchObject({ retryable: true });
    const e = await c.assertion("c2").catch((x) => x);
    expect(e).toBeInstanceOf(CredentialsError);
    expect(e).toMatchObject({ code: "HTTP_ERROR", status: 502 });
  });

  it("sends no credentials on the public verifier call", async () => {
    const { c, calls } = client([200, { success: true, data: { decision: "VERIFIED" } }]);
    expect(await c.verifyPublic("t1", "c1", "g 1")).toEqual({ decision: "VERIFIED" });
    expect(calls[0]!.url).toBe(`${BASE}/credentials/assertions/t1.c1/verify?grant=g%201`);
    expect(calls[0]!.headers.authorization).toBeUndefined();
  });

  it("names the terms of a disclosure and routes the other calls", async () => {
    const { c, calls } = client(...Array.from({ length: 9 }, () => ok({})));
    await c.createDisclosure("acme", "zoikorum", "ENGAGEMENT", ["decision"], 5, "g0");
    await c.withdrawDisclosure("g1");
    await c.readDisclosure("g1", "c1", "acme", "zoikorum", "ENGAGEMENT");
    await c.disciplinary("Sample Board", { country: "US" }, "reviewer-1", { consent_ref: "consent-1" });
    await c.verifySignedCredential("a.b.c", "d.e.f", "US");
    await c.evaluateEligibility("US", "LAW", "legal.advice", "CA");
    await c.evaluateEngagement("US", "LAW", "legal.advice", undefined, { insurance_required: false });
    await c.packs("US", "LAW");
    await c.appeal("c1", "the record is wrong", { license_ref: "L-1" });
    expect(calls[0]!.body).toEqual({ recipient: "acme", consumer: "zoikorum", purpose: "ENGAGEMENT", fields: ["decision"], days: 5, parent_grant_id: "g0" });
    expect(calls[2]!.body).toEqual({ check_id: "c1", recipient: "acme", consumer: "zoikorum", purpose: "ENGAGEMENT" });
    expect(calls[3]!.body).toMatchObject({ reviewer_principal_id: "reviewer-1", consent_ref: "consent-1" });
    expect(calls[4]!.body).toEqual({ credential: "a.b.c", holder_proof: "d.e.f", jurisdiction: "US" });
    expect(calls.map((x) => x.url.replace(BASE, ""))).toEqual([
      "/credentials/disclosures",
      "/credentials/disclosures/g1:withdraw",
      "/credentials/disclosures/g1:read",
      "/credentials/disciplinary/checks",
      "/credentials/vc/verify",
      "/v1/eligibility/professional/evaluate",
      "/v1/eligibility/engagement/evaluate",
      "/credential-packs/US/LAW",
      "/credentials/cases/c1/appeals",
    ]);
  });
});

describe("Zoikorum and Rooms packs", () => {
  it("verifies a signed decision offline, ignoring the unsigned body, and READY means ready only", async () => {
    const keys = await makeKeys();
    const token = await keys.sign(claims());
    const { c, calls } = client(ok({ signature: token, engagement: "BLOCKED", tier: "C" }));
    const d = await new ZoikorumPack(c, keys.jwks).decide("US", "LAW", "legal.advice", "CA");
    expect(d).toMatchObject({ ready: true, engagement: "READY", tier: "A", consumer: "zoikorum", token });
    expect(d.dimensions.IDENTITY).toBe("VERIFIED");
    expect(calls[0]!.url.endsWith("/v1/consumers/zoikorum/decisions")).toBe(true);
    const hold = await ZoikorumPack.verifyDecision(await keys.sign(claims({ engagement: "HOLD", tier: "NONE" })), keys.jwks);
    expect(hold.ready).toBe(false);
  });

  it("verifies Ed25519 as well", async () => {
    const keys = await makeKeys("Ed25519", "ed1");
    expect((await ZoikorumPack.verifyDecision(await keys.sign(claims()), keys.jwks)).ready).toBe(true);
  });

  it.each([
    ["wrong token type", (k: Awaited<ReturnType<typeof makeKeys>>) => k.sign(claims(), { typ: "action-grant+jwt" })],
    ["unknown key", (k: Awaited<ReturnType<typeof makeKeys>>) => k.sign(claims(), { kid: "other" })],
    ["unsigned", (k: Awaited<ReturnType<typeof makeKeys>>) => k.sign(claims(), { alg: "none" })],
    ["not a token", async () => "not-a-token"],
  ])("refuses a decision that does not verify: %s", async (_n, make) => {
    const keys = await makeKeys();
    await expect(ZoikorumPack.verifyDecision(await make(keys), keys.jwks)).rejects.toMatchObject({ code: "DECISION_SIGNATURE_INVALID" });
  });

  it("refuses a decision signed by someone else under the same key id", async () => {
    const keys = await makeKeys();
    const other = await makeKeys();
    await expect(ZoikorumPack.verifyDecision(await other.sign(claims()), keys.jwks)).rejects.toBeInstanceOf(ScopeError);
  });

  it("refuses a key that carries private members or embedded keys", async () => {
    const keys = await makeKeys();
    const leaky = { keys: [{ ...keys.jwks.keys[0], d: "secret" }] };
    await expect(ZoikorumPack.verifyDecision(await keys.sign(claims()), leaky)).rejects.toMatchObject({ code: "DECISION_SIGNATURE_INVALID" });
  });

  it("refuses a decision for another consumer, and Rooms never accepts Zoikorum's", async () => {
    const keys = await makeKeys();
    await expect(ZoikorumPack.verifyDecision(await keys.sign(claims({ consumer: "rooms" })), keys.jwks)).rejects.toMatchObject({ code: "DECISION_FOR_ANOTHER_CONSUMER" });
    const good = await ZoikorumPack.verifyDecision(await keys.sign(claims()), keys.jwks);
    expect(() => RoomsPack.acceptDecision(good)).toThrow(ScopeError);
  });

  it("lets each pack share only for its own consumer", async () => {
    const { c, calls } = client(ok({ grant_id: "g1" }), ok({ grant_id: "g2" }));
    await new ZoikorumPack(c, { keys: [] }).share("acme", "ENGAGEMENT", ["decision"]);
    await new RoomsPack(c).share("landlord", "TENANCY", ["decision"]);
    expect(calls.map((x) => x.body!.consumer)).toEqual(["zoikorum", "rooms"]);
  });
});
