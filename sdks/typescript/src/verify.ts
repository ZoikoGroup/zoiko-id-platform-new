/**
 * The Zoiko iD Verify API client for a partner's SERVER (CR-163), TypeScript. No runtime dependencies: global `fetch` (injectable) and WebCrypto, in Node 20+.
 *
 * - It holds a client id and EITHER a secret OR a private key (never both). Run it on your server; never in a web page or a mobile app.
 * - A private-key client signs a fresh single-use assertion (ES256 or EdDSA) for every token request, so Zoiko iD holds nothing that could leak.
 * - The token is cached and renewed a minute before it ends; a 401 renews it once and retries that call once, never in a loop.
 * - Every POST that makes something takes a REQUIRED `idempotencyKey`: a retry must reuse it, so the key is yours and is never generated behind your back.
 * - An error answer becomes a `VerifyError` with the API's own code. Nothing is guessed: only what the API says is returned.
 * - `verifyWebhook` checks a delivery's RFC 9421 signature and RFC 9530 digest against the published signing keys.
 */

export type VerifyFetch = (url: string, init: { method: string; headers: Record<string, string>; body?: string }) => Promise<{ status: number; text(): Promise<string> }>;
export type Json = Record<string, unknown>;

export const ASSERTION_TYPE = "urn:ietf:params:oauth:client-assertion-type:jwt-bearer";
const ASSERTION_LIFETIME = 120;
const RENEW_BEFORE = 60;
const PREFIX = "/v1/verify-api";

export class VerifyError extends Error {
  constructor(
    readonly code: string,
    readonly status: number,
    readonly detail: string = "",
    readonly retryable: boolean = false,
  ) {
    super(detail ? `${code}: ${detail}` : code);
    this.name = "VerifyError";
  }
}

export class WebhookError extends Error {
  constructor(readonly code: string) {
    super(code);
    this.name = "WebhookError";
  }
}

const enc = new TextEncoder();

function b64url(bytes: Uint8Array): string {
  let s = "";
  for (const b of bytes) s += String.fromCharCode(b);
  return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function b64urlDecode(text: string): Uint8Array<ArrayBuffer> {
  const pad = text.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - (text.length % 4)) % 4);
  return Uint8Array.from(atob(pad), (c) => c.charCodeAt(0));
}

function b64(bytes: Uint8Array): string {
  let s = "";
  for (const b of bytes) s += String.fromCharCode(b);
  return btoa(s);
}

function pemToDer(pem: string): Uint8Array<ArrayBuffer> {
  const body = pem.replace(/-----BEGIN [^-]+-----/, "").replace(/-----END [^-]+-----/, "").replace(/\s+/g, "");
  return Uint8Array.from(atob(body), (c) => c.charCodeAt(0));
}

type SigningKey = { alg: "ES256" | "EdDSA"; key: CryptoKey };

/** A PKCS#8 PEM private key, EC P-256 or Ed25519. */
async function importPrivateKey(pem: string): Promise<SigningKey> {
  const der = pemToDer(pem);
  try {
    return { alg: "ES256", key: await crypto.subtle.importKey("pkcs8", der, { name: "ECDSA", namedCurve: "P-256" }, false, ["sign"]) };
  } catch {
    try {
      return { alg: "EdDSA", key: await crypto.subtle.importKey("pkcs8", der, { name: "Ed25519" }, false, ["sign"]) };
    } catch {
      throw new Error("the private key must be a PKCS#8 EC P-256 or Ed25519 key");
    }
  }
}

async function signJws(k: SigningKey, payload: Json, keyId?: string): Promise<string> {
  const header: Json = { alg: k.alg, typ: "JWT", ...(keyId ? { kid: keyId } : {}) };
  const input = `${b64url(enc.encode(JSON.stringify(header)))}.${b64url(enc.encode(JSON.stringify(payload)))}`;
  const algorithm = k.alg === "ES256" ? { name: "ECDSA", hash: "SHA-256" } : { name: "Ed25519" };
  const sig = new Uint8Array(await crypto.subtle.sign(algorithm, k.key, enc.encode(input))); // WebCrypto gives ECDSA as r || s, which is what a JWS wants
  return `${input}.${b64url(sig)}`;
}

function errorOf(status: number, text: string): VerifyError {
  try {
    const err = (JSON.parse(text) as { error?: { code?: string; message?: string; retryable?: boolean } }).error ?? {};
    return new VerifyError(err.code ?? "ERROR", status, err.message ?? "", err.retryable ?? (status >= 500 || status === 429));
  } catch {
    return new VerifyError("ERROR", status, "The answer could not be read.", status >= 500);
  }
}

export type VerifyClientOptions = {
  clientId: string;
  clientSecret?: string;
  privateKeyPem?: string;
  keyId?: string;
  scope?: string;
  fetch?: VerifyFetch;
  /** Seconds since the epoch; injectable for tests. */
  now?: () => number;
};

export type SessionInput = {
  idempotencyKey: string;
  customerReference: string;
  country: string;
  purposeCode: string;
  workflowId?: string;
  workflowVersion?: string;
  region?: string;
  locale?: string;
  returnOrigin?: string;
  metadata?: Record<string, string>;
};

export class VerifyClient {
  private readonly base: string;
  private readonly opts: VerifyClientOptions;
  private readonly fetcher: VerifyFetch;
  private readonly now: () => number;
  private key: Promise<SigningKey> | null = null;
  private token: string | null = null;
  private expiresAt = 0;

  constructor(baseUrl: string, opts: VerifyClientOptions) {
    if ((opts.clientSecret === undefined) === (opts.privateKeyPem === undefined)) throw new Error("give exactly one of clientSecret or privateKeyPem");
    this.base = baseUrl.replace(/\/+$/, "");
    this.opts = opts;
    this.fetcher = opts.fetch ?? ((url, init) => fetch(url, init));
    this.now = opts.now ?? (() => Date.now() / 1000);
  }

  /** Never shows a secret, a key or a token. */
  toString(): string {
    return `VerifyClient(${this.base}, ${this.opts.clientId})`;
  }

  toJSON(): Json {
    return { baseUrl: this.base, clientId: this.opts.clientId };
  }

  private async tokenBody(): Promise<string> {
    const body: Record<string, string> = { grant_type: "client_credentials" };
    if (this.opts.scope) body.scope = this.opts.scope;
    if (this.opts.privateKeyPem !== undefined) {
      this.key ??= importPrivateKey(this.opts.privateKeyPem);
      const now = Math.floor(this.now());
      body.client_assertion_type = ASSERTION_TYPE;
      body.client_assertion = await signJws(
        await this.key,
        { iss: this.opts.clientId, sub: this.opts.clientId, aud: `${this.base}${PREFIX}/oauth2/token`, iat: now, exp: now + ASSERTION_LIFETIME, jti: crypto.randomUUID().replace(/-/g, "") },
        this.opts.keyId,
      );
    } else {
      body.client_id = this.opts.clientId;
      body.client_secret = this.opts.clientSecret ?? "";
    }
    return JSON.stringify(body);
  }

  private async ensureToken(force = false): Promise<string> {
    if (!force && this.token && this.now() < this.expiresAt - RENEW_BEFORE) return this.token;
    const res = await this.fetcher(`${this.base}${PREFIX}/oauth2/token`, { method: "POST", headers: { "content-type": "application/json", accept: "application/json" }, body: await this.tokenBody() });
    const text = await res.text();
    if (res.status !== 200) throw errorOf(res.status, text);
    const answer = JSON.parse(text) as { access_token: string; expires_in?: number };
    this.token = answer.access_token;
    this.expiresAt = this.now() + (answer.expires_in ?? 900);
    return this.token;
  }

  private async call(method: string, path: string, body?: Json, opts: { idempotencyKey?: string; query?: Record<string, string | number | undefined> } = {}): Promise<Json> {
    let url = `${this.base}${PREFIX}${path}`;
    const q = Object.entries(opts.query ?? {}).filter(([, v]) => v !== undefined);
    if (q.length) url += "?" + new URLSearchParams(q.map(([k, v]) => [k, String(v)])).toString();
    for (const attempt of [0, 1]) {
      const headers: Record<string, string> = { authorization: `Bearer ${await this.ensureToken(attempt === 1)}`, accept: "application/json" };
      if (body !== undefined) headers["content-type"] = "application/json";
      if (opts.idempotencyKey) headers["idempotency-key"] = opts.idempotencyKey;
      const res = await this.fetcher(url, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
      const text = await res.text();
      if (res.status === 401 && attempt === 0) {
        this.token = null; // renew once and retry that call once
        continue;
      }
      if (res.status >= 400) throw errorOf(res.status, text);
      return text ? (JSON.parse(text) as Json) : {};
    }
    throw new VerifyError("AUTH_INVALID", 401, "The token was refused after it was renewed.");
  }

  whoami = (): Promise<Json> => this.call("GET", "/whoami");
  workflows = (): Promise<Json> => this.call("GET", "/workflows");
  products = (): Promise<Json> => this.call("GET", "/products");

  /** Start a verification. `launch_url` is in this answer once: send it to your customer. A created session is not an identity approval. */
  createSession(i: SessionInput): Promise<Json> {
    const body: Json = { customer_reference: i.customerReference, country: i.country, purpose_code: i.purposeCode };
    const optional: Array<[string, unknown]> = [["workflow_id", i.workflowId], ["workflow_version", i.workflowVersion], ["region", i.region], ["locale", i.locale], ["return_origin", i.returnOrigin], ["metadata", i.metadata]];
    for (const [k, v] of optional) if (v !== undefined) body[k] = v;
    return this.call("POST", "/verification-sessions", body, { idempotencyKey: i.idempotencyKey });
  }

  getSession = (id: string): Promise<Json> => this.call("GET", `/verification-sessions/${id}`);
  /** The minimized decision: outcome, assurance, checks, reason codes, version. Never photographs or document data. */
  getDecision = (id: string): Promise<Json> => this.call("GET", `/verification-sessions/${id}/decision`);
  listSessions = (q: { status?: string; customerReference?: string; createdFrom?: string; createdTo?: string; cursor?: string; limit?: number } = {}): Promise<Json> =>
    this.call("GET", "/verification-sessions", undefined, { query: { status: q.status, customer_reference: q.customerReference, created_from: q.createdFrom, created_to: q.createdTo, cursor: q.cursor, limit: q.limit } });
  cancelSession = (id: string): Promise<Json> => this.call("POST", `/verification-sessions/${id}:cancel`, {});
  /** A fresh launch link for a customer who left. The old link stops working. */
  resumeToken = (id: string): Promise<Json> => this.call("POST", `/verification-sessions/${id}/resume-token`, {});
  /** A new verification that follows a finished one. The original is left untouched. */
  recheck = (id: string, idempotencyKey: string): Promise<Json> => this.call("POST", `/verification-sessions/${id}:recheck`, {}, { idempotencyKey });
  /** Sandbox only: a session already at a simulated outcome. */
  sandboxSession = (outcome: string, idempotencyKey: string, customerReference?: string): Promise<Json> =>
    this.call("POST", `/sandbox/fixtures/${outcome}/sessions`, customerReference ? { customer_reference: customerReference } : {}, { idempotencyKey });
  /** Sandbox only: move an open session to a simulated outcome. */
  simulate = (id: string, outcome: string): Promise<Json> => this.call("POST", `/sandbox/sessions/${id}:simulate`, { outcome });
}

// ------------------------------------------------------------------------------------------------ webhooks

const SIGNED = ["@method", "@authority", "@target-uri", "content-type", "content-digest", "x-zoiko-event-id"] as const;
export const MAX_SIGNATURE_AGE = 300;

export async function contentDigest(body: Uint8Array): Promise<string> {
  return `sha-256=:${b64(new Uint8Array(await crypto.subtle.digest("SHA-256", new Uint8Array(body))))}:`;
}

async function importPublic(jwk: Record<string, unknown>): Promise<{ key: CryptoKey; algorithm: AlgorithmIdentifier | EcdsaParams }> {
  if (jwk.kty === "EC" && jwk.crv === "P-256") {
    const key = await crypto.subtle.importKey("jwk", { kty: "EC", crv: "P-256", x: jwk.x as string, y: jwk.y as string, ext: true }, { name: "ECDSA", namedCurve: "P-256" }, false, ["verify"]);
    return { key, algorithm: { name: "ECDSA", hash: "SHA-256" } };
  }
  if (jwk.kty === "OKP" && jwk.crv === "Ed25519") {
    const key = await crypto.subtle.importKey("jwk", { kty: "OKP", crv: "Ed25519", x: jwk.x as string, ext: true }, { name: "Ed25519" }, false, ["verify"]);
    return { key, algorithm: { name: "Ed25519" } };
  }
  throw new Error("only EC P-256 and Ed25519 public keys");
}

/** Returns the event id when the delivery is genuine; throws `WebhookError` otherwise. Pass the EXACT raw body bytes and the published signing keys. There is no unsigned path. */
export async function verifyWebhook(args: { url: string; body: Uint8Array; headers: Record<string, string>; keys: Array<Record<string, unknown>>; now?: number; maxAge?: number }): Promise<string> {
  const h: Record<string, string> = {};
  for (const [k, v] of Object.entries(args.headers)) h[k.toLowerCase()] = v;
  const input = h["signature-input"];
  const signature = h["signature"];
  if (!input || !signature) throw new WebhookError("WEBHOOK_SIGNATURE_MISSING");
  const m = /^zid=(\(.*\));created=(\d+);keyid="([^"]+)"$/.exec(input);
  if (!m || !signature.startsWith("zid=:") || !signature.endsWith(":")) throw new WebhookError("WEBHOOK_SIGNATURE_MALFORMED");
  const [, components, createdText, keyId] = m as unknown as [string, string, string, string];
  if (components !== `(${SIGNED.map((c) => `"${c}"`).join(" ")})`) throw new WebhookError("WEBHOOK_SIGNATURE_MALFORMED"); // a signature that covers less than the full set is refused
  const created = Number(createdText);
  const now = args.now ?? Date.now() / 1000;
  if (Math.abs(now - created) > (args.maxAge ?? MAX_SIGNATURE_AGE)) throw new WebhookError("WEBHOOK_SIGNATURE_STALE");
  if (h["content-digest"] !== (await contentDigest(args.body))) throw new WebhookError("WEBHOOK_DIGEST_MISMATCH");
  const jwk = args.keys.find((k) => k.kid === keyId);
  if (!jwk) throw new WebhookError("WEBHOOK_KEY_UNKNOWN"); // a retired key is gone: no fallback
  const values: Record<string, string> = {
    "@method": "POST",
    "@authority": new URL(args.url).host.toLowerCase(),
    "@target-uri": args.url,
    "content-type": h["content-type"] ?? "",
    "content-digest": h["content-digest"]!,
    "x-zoiko-event-id": h["x-zoiko-event-id"] ?? "",
  };
  const base = SIGNED.map((c) => `"${c}": ${values[c]}`).join("\n") + `\n"@signature-params": ${components};created=${created};keyid="${keyId}"`;
  let ok = false;
  try {
    const { key, algorithm } = await importPublic(jwk);
    ok = await crypto.subtle.verify(algorithm, key, b64Decode(signature.slice(5, -1)), enc.encode(base));
  } catch {
    ok = false;
  }
  if (!ok) throw new WebhookError("WEBHOOK_SIGNATURE_INVALID");
  return values["x-zoiko-event-id"]!;
}

function b64Decode(text: string): Uint8Array<ArrayBuffer> {
  return Uint8Array.from(atob(text), (c) => c.charCodeAt(0));
}

export { b64url as _b64url, b64urlDecode as _b64urlDecode };
