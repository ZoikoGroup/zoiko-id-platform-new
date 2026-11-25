/**
 * Zoiko iD credential-trust client and consumer packs (ZID-GAP-001 v2.0 GID-063; CR-069), TypeScript.
 *
 * The same contract as the Python SDK (`zoiko_credentials`): one client, two thin packs. A pack can only narrow what the client does and
 * the server enforces every rule regardless.
 *  - A Zoikorum decision is signed for the consumer "zoikorum". `verifyDecision` checks the signature offline against pinned keys
 *    (ES256 or Ed25519, no remote keys) and that the decision is for Zoikorum; the Rooms pack refuses it.
 *  - A disclosure made through a pack is always for that pack's consumer.
 *  - An answer is the `data` of the platform envelope; an error becomes `CredentialsError(code, status)`.
 *
 * No runtime dependencies: global `fetch` (injectable) and WebCrypto (`crypto.subtle`), in Node 20+ and browsers.
 */

export const DECISION_TYP = "zid-decision+jwt";
export const ASSERTION_TYP = "zid-assertion+jwt";

export const CHECK_PATHS = {
  academic: "academic",
  license: "professional-licenses",
  certification: "certifications",
  employment: "experience",
  cpd: "continuing-education",
  insurance: "insurance",
} as const;
export type ClaimKind = keyof typeof CHECK_PATHS;

export type Jwks = { keys: Array<Record<string, unknown>> };
export type Json = Record<string, unknown>;
export type FetchLike = (url: string, init: { method: string; headers: Record<string, string>; body?: string }) => Promise<{
  status: number;
  text(): Promise<string>;
}>;

/** The platform refused or failed a call. `code` is its stable code (for example PACK_UNAVAILABLE). */
export class CredentialsError extends Error {
  constructor(
    readonly code: string,
    readonly status: number,
    message = "",
    readonly retryable = false,
  ) {
    super(message ? `${code}: ${message}` : code);
    this.name = "CredentialsError";
  }
}

/** A pack was asked to do something outside its consumer's scope. Raised before any network call. */
export class ScopeError extends Error {
  constructor(
    readonly code: string,
    detail = "",
  ) {
    super(detail ? `${code}: ${detail}` : code);
    this.name = "ScopeError";
  }
}

export class CredentialsClient {
  private readonly base: string;

  constructor(
    baseUrl: string,
    private readonly accessToken: string,
    private readonly fetcher: FetchLike = (url, init) => fetch(url, init),
  ) {
    this.base = baseUrl.replace(/\/+$/, "");
  }

  /** @internal used by the packs */
  async call(method: string, path: string, body?: Json, auth = true): Promise<Json> {
    const headers: Record<string, string> = { accept: "application/json" };
    if (auth) headers.authorization = `Bearer ${this.accessToken}`;
    if (body !== undefined) headers["content-type"] = "application/json";
    const res = await this.fetcher(this.base + path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
    let doc: Json = {};
    try {
      const raw = await res.text();
      doc = raw ? (JSON.parse(raw) as Json) : {};
    } catch {
      doc = {};
    }
    if (res.status >= 400 || doc.success === false) {
      const err = (doc.error ?? {}) as Json;
      throw new CredentialsError(String(err.code ?? "HTTP_ERROR"), res.status, String(err.message ?? ""), Boolean(err.retryable));
    }
    return (doc.data ?? doc) as Json;
  }

  check(kind: ClaimKind, claim: Json, jurisdiction: Record<string, string>, opts: Json = {}): Promise<Json> {
    if (!(kind in CHECK_PATHS)) throw new ScopeError("CLAIM_KIND_UNKNOWN", String(kind));
    return this.call("POST", `/v1/credentials/${CHECK_PATHS[kind]}/checks`, { claim, jurisdiction, ...opts });
  }

  /** A sensitive check: `opts` must carry `consent_ref` or `legal_basis_ref`, and the reviewer must not be you. */
  disciplinary(regulator: string, jurisdiction: Record<string, string>, reviewerPrincipalId: string, opts: Json = {}): Promise<Json> {
    return this.call("POST", "/v1/credentials/disciplinary/checks", { regulator, jurisdiction, reviewer_principal_id: reviewerPrincipalId, ...opts });
  }

  verifySignedCredential(credential: string, holderProof?: string, jurisdiction?: string): Promise<Json> {
    const body: Json = { credential };
    if (holderProof) body.holder_proof = holderProof;
    if (jurisdiction) body.jurisdiction = jurisdiction;
    return this.call("POST", "/v1/credentials/vc/verify", body);
  }

  assertion(checkId: string): Promise<Json> {
    return this.call("GET", `/v1/credentials/assertions/${checkId}`);
  }

  evaluateEligibility(country: string, professionCode: string, serviceCode: string, subdivision?: string): Promise<Json> {
    const body: Json = { country, profession_code: professionCode, service_code: serviceCode };
    if (subdivision) body.subdivision = subdivision;
    return this.call("POST", "/v1/eligibility/professional/evaluate", body);
  }

  evaluateEngagement(country: string, professionCode: string, serviceCode: string, subdivision?: string, opts: Json = {}): Promise<Json> {
    const body: Json = { country, profession_code: professionCode, service_code: serviceCode, ...opts };
    if (subdivision) body.subdivision = subdivision;
    return this.call("POST", "/v1/eligibility/engagement/evaluate", body);
  }

  packs(country: string, profession: string): Promise<Json> {
    return this.call("GET", `/v1/credential-packs/${country}/${profession}`);
  }

  createDisclosure(recipient: string, consumer: string, purpose: string, fields: string[], days = 30, parentGrantId?: string): Promise<Json> {
    const body: Json = { recipient, consumer, purpose, fields, days };
    if (parentGrantId) body.parent_grant_id = parentGrantId;
    return this.call("POST", "/v1/credentials/disclosures", body);
  }

  withdrawDisclosure(grantId: string): Promise<Json> {
    return this.call("POST", `/v1/credentials/disclosures/${grantId}:withdraw`, {});
  }

  readDisclosure(grantId: string, checkId: string, recipient: string, consumer: string, purpose: string): Promise<Json> {
    return this.call("POST", `/v1/credentials/disclosures/${grantId}:read`, { check_id: checkId, recipient, consumer, purpose });
  }

  /** The bounded public view; needs no account, only a PUBLIC_VERIFIER grant the holder shared. */
  verifyPublic(tenantId: string, checkId: string, grant: string): Promise<Json> {
    return this.call("GET", `/v1/credentials/assertions/${tenantId}.${checkId}/verify?grant=${encodeURIComponent(grant)}`, undefined, false);
  }

  appeal(checkId: string, reason: string, correction: Record<string, string> = {}): Promise<Json> {
    return this.call("POST", `/v1/credentials/cases/${checkId}/appeals`, { reason, correction });
  }
}

// ------------------------------------------------------------------------------------------------ signed decisions

const b64d = (s: string): Uint8Array => {
  const pad = "=".repeat((4 - (s.length % 4)) % 4);
  const bin = atob(s.replace(/-/g, "+").replace(/_/g, "/") + pad);
  return Uint8Array.from(bin, (c) => c.charCodeAt(0));
};
const text = (b: Uint8Array): string => new TextDecoder().decode(b);

/** Verify a compact JWS offline against pinned keys, for one token type. ES256 or Ed25519 only; remote or embedded keys are never
 * followed. Throws `ScopeError("DECISION_SIGNATURE_INVALID")` on anything that does not verify. */
export async function verifySigned(token: string, jwks: Jwks, typ: string): Promise<Json> {
  const bad = (d: string) => new ScopeError("DECISION_SIGNATURE_INVALID", d);
  const parts = typeof token === "string" ? token.split(".") : [];
  if (parts.length !== 3) throw bad("not a compact JWS");
  let header: Json, payload: Json, sig: Uint8Array;
  try {
    header = JSON.parse(text(b64d(parts[0]!))) as Json;
    payload = JSON.parse(text(b64d(parts[1]!))) as Json;
    sig = b64d(parts[2]!);
  } catch {
    throw bad("malformed token");
  }
  const alg = header.alg;
  if (alg !== "ES256" && alg !== "Ed25519") throw bad(`algorithm not allowed: ${String(alg)}`);
  if (header.typ !== typ) throw bad("wrong token type");
  for (const k of ["jku", "x5u", "x5c", "jwk"]) if (k in header) throw bad("remote or embedded keys are never followed");
  const matches = (jwks.keys ?? []).filter((k) => k.kid === header.kid);
  if (!header.kid || matches.length !== 1) throw bad("unknown key id");
  const jwk = matches[0]!;
  if (["d", "p", "q", "k"].some((m) => m in jwk)) throw bad("key carries private members");
  if (jwk.alg !== undefined && jwk.alg !== alg) throw bad("key algorithm mismatch");
  const data = new TextEncoder().encode(`${parts[0]}.${parts[1]}`);
  try {
    const key =
      alg === "ES256"
        ? await crypto.subtle.importKey("jwk", jwk as JsonWebKey, { name: "ECDSA", namedCurve: "P-256" }, false, ["verify"])
        : await crypto.subtle.importKey("jwk", jwk as JsonWebKey, { name: "Ed25519" }, false, ["verify"]);
    const ok =
      alg === "ES256"
        ? sig.length === 64 && (await crypto.subtle.verify({ name: "ECDSA", hash: "SHA-256" }, key, sig as BufferSource, data))
        : await crypto.subtle.verify({ name: "Ed25519" }, key, sig as BufferSource, data);
    if (!ok) throw bad("signature does not verify");
  } catch (e) {
    if (e instanceof ScopeError) throw e;
    throw bad("unusable key");
  }
  return payload;
}

// ------------------------------------------------------------------------------------------------- signed assertions

const NOT_PART_OF_THE_BODY = new Set(["signature", "superseded"]);

/** JSON with sorted keys and no spaces: the same bytes as the server's canonical body (Python `sort_keys`, `(",", ":")`, UTF-8). */
function canonicalJson(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (value !== null && typeof value === "object") {
    const o = value as Record<string, unknown>;
    return `{${Object.keys(o)
      .sort()
      .map((k) => `${JSON.stringify(k)}:${canonicalJson(o[k])}`)
      .join(",")}}`;
  }
  return JSON.stringify(value);
}

/** SHA-256 (hex) of the canonical body of a credential assertion, without `signature` and `superseded`. Equal to the server's. */
export async function assertionDigest(assertion: Json): Promise<string> {
  const body = Object.fromEntries(Object.entries(assertion).filter(([k]) => !NOT_PART_OF_THE_BODY.has(k)));
  const bytes = new TextEncoder().encode(canonicalJson(body));
  const hash = new Uint8Array(await crypto.subtle.digest("SHA-256", bytes as BufferSource));
  return Array.from(hash, (b) => b.toString(16).padStart(2, "0")).join("");
}

/** Check offline that an assertion is signed by the platform and that its body has not changed. The signature covers a digest of
 * every field, so verifying the body is the point. Throws `ScopeError` ASSERTION_UNSIGNED, ASSERTION_SIGNATURE_INVALID or
 * ASSERTION_TAMPERED. */
export async function verifyAssertion(assertion: Json, jwks: Jwks): Promise<Json> {
  const token = assertion.signature;
  if (typeof token !== "string" || !token) throw new ScopeError("ASSERTION_UNSIGNED");
  let claims: Json;
  try {
    claims = await verifySigned(token, jwks, ASSERTION_TYP);
  } catch (e) {
    throw new ScopeError("ASSERTION_SIGNATURE_INVALID", e instanceof Error ? e.message : "");
  }
  if (
    claims.typ !== "zid-assertion" ||
    claims.check_id !== assertion.check_id ||
    claims.assertion_id !== assertion.assertion_id ||
    claims.body_sha256 !== (await assertionDigest(assertion))
  ) {
    throw new ScopeError("ASSERTION_TAMPERED");
  }
  return assertion;
}

// ------------------------------------------------------------------------------------------------------- Zoikorum

export type ZoikorumDecision = {
  decisionId: string;
  engagement: string; // READY | HOLD | BLOCKED
  tier: string; // A | B | C | NONE
  dimensions: Record<string, string>; // five separate statuses, never one badge
  reasonCodes: string[];
  consumer: string;
  token: string;
  /** READY and nothing else: a tier or a public badge is never an authorization. */
  ready: boolean;
};

export class ZoikorumPack {
  static readonly CONSUMER = "zoikorum";

  constructor(
    private readonly client: CredentialsClient,
    private readonly jwks: Jwks,
  ) {}

  async decide(country: string, professionCode: string, serviceCode: string, subdivision?: string, opts: Json = {}): Promise<ZoikorumDecision> {
    const body: Json = { country, profession_code: professionCode, service_code: serviceCode, ...opts };
    if (subdivision) body.subdivision = subdivision;
    const out = await this.client.call("POST", "/v1/consumers/zoikorum/decisions", body);
    return ZoikorumPack.verifyDecision(String(out.signature), this.jwks);
  }

  /** Never trusts the body of a response over its signed claims. */
  static async verifyDecision(token: string, jwks: Jwks): Promise<ZoikorumDecision> {
    const claims = await verifySigned(token, jwks, DECISION_TYP);
    if (claims.consumer !== ZoikorumPack.CONSUMER) throw new ScopeError("DECISION_FOR_ANOTHER_CONSUMER", String(claims.consumer));
    const engagement = String(claims.engagement);
    return {
      decisionId: String(claims.decision_id),
      engagement,
      tier: String(claims.tier),
      dimensions: { ...((claims.dimensions as Record<string, string>) ?? {}) },
      reasonCodes: [...((claims.reason_codes as string[]) ?? [])],
      consumer: String(claims.consumer),
      token,
      ready: engagement === "READY",
    };
  }

  share(recipient: string, purpose: string, fields: string[], days = 30): Promise<Json> {
    return this.client.createDisclosure(recipient, ZoikorumPack.CONSUMER, purpose, fields, days);
  }
}

// ---------------------------------------------------------------------------------------------------------- Rooms

export class RoomsPack {
  static readonly CONSUMER = "rooms";

  constructor(private readonly client: CredentialsClient) {}

  share(recipient: string, purpose: string, fields: string[], days = 30): Promise<Json> {
    return this.client.createDisclosure(recipient, RoomsPack.CONSUMER, purpose, fields, days);
  }

  /** Always refuses: a Zoikorum professional decision is not usable for a tenancy (GLB-33). */
  static acceptDecision(decision: ZoikorumDecision): never {
    throw new ScopeError("DECISION_FOR_ANOTHER_CONSUMER", decision.consumer);
  }
}

export * from "./session.js";
export * from "./v2.js";
export * from "./widget.js";
export * from "./chip-relay.js";
export { verifyBundle, canonicalize as canonicalizeEvidence, BUNDLE_FORMAT as EVIDENCE_BUNDLE_FORMAT, type Report as EvidenceReport, type Check as EvidenceCheck } from "./evidence-verify.js";
export { errorClassOf, ERROR_CLASSES, type ErrorClassEntry } from "./error-classes.js";
