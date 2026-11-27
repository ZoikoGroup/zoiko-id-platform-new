import { describe, expect, it } from "vitest";

import fixture from "./fixtures/platform-webhook.json";
import { _b64urlDecode, ASSERTION_TYPE, VerifyClient, VerifyError, verifyWebhook, WebhookError, type VerifyFetch } from "../src/verify.js";

const BASE = "https://api.example.test";
const TOKEN = `${BASE}/v1/verify-api/oauth2/token`;

type Call = { method: string; url: string; headers: Record<string, string>; body?: unknown };

/** A fake fetch: records calls, answers from a script, then falls back to a normal answer. */
function fake(script: Array<[number, unknown]> = []) {
  const calls: Call[] = [];
  const state = { tokens: 0, script };
  const fetcher: VerifyFetch = async (url, init) => {
    calls.push({ method: init.method, url, headers: init.headers, body: init.body ? JSON.parse(init.body) : undefined });
    const next = state.script.shift();
    if (next) return { status: next[0], text: async () => JSON.stringify(next[1]) };
    if (url === TOKEN) {
      state.tokens += 1;
      return { status: 200, text: async () => JSON.stringify({ access_token: `zpt_${state.tokens}`, token_type: "Bearer", expires_in: 900 }) };
    }
    return { status: 200, text: async () => JSON.stringify({ ok: true }) };
  };
  return { fetcher, calls, state };
}

function clock(start = 1_800_000_000) {
  const c = { now: start };
  return { c, fn: () => c.now };
}

function secretClient(f: ReturnType<typeof fake>, now: () => number) {
  return new VerifyClient(BASE, { clientId: "zpc_abc", clientSecret: "zps_secret", fetch: f.fetcher, now });
}

function toPem(der: ArrayBuffer): string {
  const b64 = btoa(String.fromCharCode(...new Uint8Array(der)));
  return `-----BEGIN PRIVATE KEY-----\n${b64.match(/.{1,64}/g)!.join("\n")}\n-----END PRIVATE KEY-----\n`;  // zid-secret-scan: allow: test key shape, not a key
}

describe("the Verify API client (CR-163)", () => {
  it("needs exactly one kind of credential and never shows it", () => {
    const f = fake();
    expect(() => new VerifyClient(BASE, { clientId: "c" })).toThrow(/exactly one/);
    expect(() => new VerifyClient(BASE, { clientId: "c", clientSecret: "s", privateKeyPem: "x" })).toThrow(/exactly one/);
    const c = secretClient(f, () => 1);
    expect(String(c)).not.toContain("zps_secret");
    expect(JSON.stringify(c)).not.toContain("zps_secret");
  });

  it("gets a token once and reuses it until just before it ends", async () => {
    const f = fake();
    const t = clock();
    const c = secretClient(f, t.fn);
    await c.whoami();
    await c.workflows();
    expect(f.state.tokens).toBe(1);
    expect(f.calls[0]!.body).toEqual({ grant_type: "client_credentials", client_id: "zpc_abc", client_secret: "zps_secret" });
    expect(f.calls[1]!.headers.authorization).toBe("Bearer zpt_1");
    t.c.now += 900 - 30;
    await c.products();
    expect(f.state.tokens).toBe(2);
    expect(f.calls.at(-1)!.headers.authorization).toBe("Bearer zpt_2");
  });

  it("renews on a 401 and retries that call once, never in a loop", async () => {
    const f = fake();
    const c = secretClient(f, clock().fn);
    await c.whoami();
    f.state.script = [[401, { error: { code: "AUTH_INVALID", message: "ended" } }]];
    expect(await c.whoami()).toEqual({ ok: true });
    expect(f.state.tokens).toBe(2);
    const g = fake();
    const d = secretClient(g, clock().fn);
    await d.whoami();
    g.state.script = [[401, { error: { code: "AUTH_INVALID" } }], [200, { access_token: "zpt_x", expires_in: 900 }], [401, { error: { code: "AUTH_INVALID", message: "still no" } }]];
    await expect(d.whoami()).rejects.toMatchObject({ code: "AUTH_INVALID", status: 401 });
    expect(g.calls.filter((x) => x.url.endsWith("/whoami")).length).toBe(3);
  });

  for (const kind of ["ES256", "EdDSA"] as const) {
    it(`a private-key client (${kind}) signs a short single-use assertion for this endpoint`, async () => {
      const algo = kind === "ES256" ? ({ name: "ECDSA", namedCurve: "P-256" } as const) : ({ name: "Ed25519" } as const);
      const pair = (await crypto.subtle.generateKey(algo, true, ["sign", "verify"])) as CryptoKeyPair;
      const pem = toPem(await crypto.subtle.exportKey("pkcs8", pair.privateKey));
      const f = fake();
      const t = clock();
      const c = new VerifyClient(BASE, { clientId: "zpc_abc", privateKeyPem: pem, keyId: "k1", fetch: f.fetcher, now: t.fn });
      await c.whoami();
      const body = f.calls[0]!.body as Record<string, string>;
      expect(body.client_secret).toBeUndefined();
      expect(body.client_assertion_type).toBe(ASSERTION_TYPE);
      const [head, payload, sig] = body.client_assertion!.split(".") as [string, string, string];
      const header = JSON.parse(new TextDecoder().decode(_b64urlDecode(head)));
      const claims = JSON.parse(new TextDecoder().decode(_b64urlDecode(payload)));
      expect(header).toEqual({ alg: kind, typ: "JWT", kid: "k1" });
      expect(claims.iss).toBe("zpc_abc");
      expect(claims.sub).toBe("zpc_abc");
      expect(claims.aud).toBe(TOKEN);
      expect(claims.exp - claims.iat).toBe(120);
      expect(claims.jti).toHaveLength(32);
      const verifyAlg = kind === "ES256" ? { name: "ECDSA", hash: "SHA-256" } : { name: "Ed25519" };
      const ok = await crypto.subtle.verify(verifyAlg, pair.publicKey, _b64urlDecode(sig), new TextEncoder().encode(`${head}.${payload}`));
      expect(ok).toBe(true);
      const tampered = await crypto.subtle.verify(verifyAlg, pair.publicKey, _b64urlDecode(sig), new TextEncoder().encode(`${head}.${payload}x`));
      expect(tampered).toBe(false);
      // a new token request carries a different assertion id: no assertion is ever sent twice
      t.c.now += 2000;
      await c.whoami();
      const tokenCalls = f.calls.filter((x) => x.url === TOKEN);
      const again = JSON.parse(new TextDecoder().decode(_b64urlDecode((tokenCalls[1]!.body as Record<string, string>).client_assertion!.split(".")[1]!)));
      expect(again.jti).not.toBe(claims.jti);
    });
  }

  it("refuses a key that is not EC P-256 or Ed25519", async () => {
    const c = new VerifyClient(BASE, { clientId: "zpc_abc", privateKeyPem: "-----BEGIN PRIVATE KEY-----\nAAAA\n-----END PRIVATE KEY-----", fetch: fake().fetcher, now: () => 1 });  // zid-secret-scan: allow: test key shape, not a key
    await expect(c.whoami()).rejects.toThrow(/EC P-256 or Ed25519/);
  });

  it("starts a session with only what was given and the callers own idempotency key", async () => {
    const f = fake();
    const c = secretClient(f, clock().fn);
    await c.createSession({ idempotencyKey: "order-1001-verify", customerReference: "cust-1", country: "US", purposeCode: "customer_onboarding", locale: "en-US", metadata: { k: "v" } });
    const call = f.calls.at(-1)!;
    expect(call.method).toBe("POST");
    expect(call.url).toBe(`${BASE}/v1/verify-api/verification-sessions`);
    expect(call.headers["idempotency-key"]).toBe("order-1001-verify");
    expect(call.body).toEqual({ customer_reference: "cust-1", country: "US", purpose_code: "customer_onboarding", locale: "en-US", metadata: { k: "v" } });
  });

  it("uses the documented paths", async () => {
    const f = fake();
    const c = secretClient(f, clock().fn);
    await c.getSession("vfs_1");
    await c.getDecision("vfs_1");
    await c.cancelSession("vfs_1");
    await c.resumeToken("vfs_1");
    await c.recheck("vfs_1", "re-1");
    await c.listSessions({ status: "completed", limit: 5 });
    await c.sandboxSession("approved", "sb-1");
    await c.simulate("vfs_1", "declined");
    const paths = f.calls.filter((x) => x.url !== TOKEN).map((x) => `${x.method} ${x.url.replace(`${BASE}/v1/verify-api`, "")}`);
    expect(paths).toEqual([
      "GET /verification-sessions/vfs_1",
      "GET /verification-sessions/vfs_1/decision",
      "POST /verification-sessions/vfs_1:cancel",
      "POST /verification-sessions/vfs_1/resume-token",
      "POST /verification-sessions/vfs_1:recheck",
      "GET /verification-sessions?status=completed&limit=5",
      "POST /sandbox/fixtures/approved/sessions",
      "POST /sandbox/sessions/vfs_1:simulate",
    ]);
  });

  it("turns an error answer into an error with the APIs own code", async () => {
    const f = fake();
    const c = secretClient(f, clock().fn);
    await c.whoami();
    f.state.script = [[409, { error: { code: "INVALID_IDEMPOTENCY_REUSE", message: "reused" } }]];
    const e = await c.createSession({ idempotencyKey: "k-1234567", customerReference: "c", country: "US", purposeCode: "p" }).catch((x: unknown) => x);
    expect(e).toBeInstanceOf(VerifyError);
    expect(e).toMatchObject({ code: "INVALID_IDEMPOTENCY_REUSE", status: 409, retryable: false });
    f.state.script = [[429, { error: { code: "RATE_LIMITED", message: "wait" } }]];
    await expect(c.whoami()).rejects.toMatchObject({ code: "RATE_LIMITED", retryable: true });
    f.state.script = [[502, {}]];
    await expect(c.whoami()).rejects.toMatchObject({ status: 502, retryable: true });
  });
});

type Case = { jwk: Record<string, unknown>; headers: Record<string, string> };
const enc = new TextEncoder();

describe("webhook verification against deliveries the platform itself signed", () => {
  for (const kind of ["ES256", "EdDSA"] as const) {
    const c = (fixture.cases as Record<string, Case>)[kind]!;
    const base = { url: fixture.url, body: enc.encode(fixture.body), headers: c.headers, keys: [c.jwk], now: fixture.created + 5 };

    it(`${kind}: a genuine delivery returns its event id`, async () => {
      expect(await verifyWebhook(base)).toBe("evt_1");
    });

    it(`${kind}: every way a delivery can be wrong is refused`, async () => {
      const code = async (over: Partial<typeof base>, headers?: Record<string, string>) =>
        verifyWebhook({ ...base, ...over, headers: headers ?? base.headers }).then(
          () => "ACCEPTED",
          (e: unknown) => (e as WebhookError).code,
        );
      expect(await code({}, {})).toBe("WEBHOOK_SIGNATURE_MISSING");
      expect(await code({}, { ...c.headers, "Signature-Input": "garbage" })).toBe("WEBHOOK_SIGNATURE_MALFORMED");
      expect(await code({ body: enc.encode(fixture.body + " ") })).toBe("WEBHOOK_DIGEST_MISMATCH");
      expect(await code({ now: fixture.created + 400 })).toBe("WEBHOOK_SIGNATURE_STALE");
      expect(await code({ keys: [] })).toBe("WEBHOOK_KEY_UNKNOWN");
      expect(await code({}, { ...c.headers, "X-Zoiko-Event-ID": "evt_2" })).toBe("WEBHOOK_SIGNATURE_INVALID");
      expect(await code({ url: "https://evil.example.com/zoikoid-webhooks" })).toBe("WEBHOOK_SIGNATURE_INVALID");
      expect(await code({}, Object.fromEntries(Object.entries(c.headers).map(([k, v]) => [k.toLowerCase(), v])))).toBe("ACCEPTED"); // header names are case-insensitive
    });
  }

  it("a delivery signed by another key is refused even under the right key id", async () => {
    const es = (fixture.cases as Record<string, Case>).ES256!;
    const other = (fixture.cases as Record<string, Case>).EdDSA!;
    const wrong = { ...other.jwk, kid: es.jwk.kid };
    await expect(verifyWebhook({ url: fixture.url, body: enc.encode(fixture.body), headers: es.headers, keys: [wrong], now: fixture.created })).rejects.toMatchObject({ code: "WEBHOOK_SIGNATURE_INVALID" });
  });
});
