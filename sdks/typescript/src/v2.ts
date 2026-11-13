/**
 * The /v2 global trust API client (ZID-INT-002, Document 02 v2.0), TypeScript. The same contract as the Python `V2Client`.
 *
 *  - errors are RFC 9457 problems (`code`, `status`, `detail`, `trace_id`), thrown as `V2Error`;
 *  - every POST that makes something takes a REQUIRED `idempotencyKey`: a retry must reuse the same key, so it is the caller's and is
 *    never generated behind their back (the server answers a replay with the stored result, and a changed body under one key is
 *    `idempotency_conflict`);
 *  - answers are the plain JSON document (no envelope);
 *  - the SDK never holds a provider secret and calls only this platform.
 *
 * No runtime dependencies: global `fetch` (injectable) and WebCrypto (`crypto.subtle`), in Node 20+ and browsers.
 */

type Json = Record<string, unknown>;
export type V2Fetch = (
  url: string,
  init: { method: string; headers: Record<string, string>; body?: string | Uint8Array },
) => Promise<{ status: number; text(): Promise<string> }>;

export class V2Error extends Error {
  constructor(
    readonly code: string,
    readonly status: number,
    readonly detail = "",
    readonly traceId: string | null = null,
    readonly retryable = false,
  ) {
    super(detail ? `${code}: ${detail}` : code);
    this.name = "V2Error";
  }
}

async function sha256Hex(bytes: Uint8Array): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", bytes as unknown as BufferSource);
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

function toBase64(bytes: Uint8Array): string {
  let s = "";
  for (const b of bytes) s += String.fromCharCode(b);
  return btoa(s);
}

type Call = { body?: Json; idempotencyKey?: string; raw?: Uint8Array; headers?: Record<string, string> };

export class V2Client {
  private readonly base: string;

  constructor(
    baseUrl: string,
    private readonly accessToken: string,
    private readonly fetcher: V2Fetch = (url, init) => fetch(url, init as RequestInit),
  ) {
    this.base = baseUrl.replace(/\/+$/, "");
  }

  private async call(method: string, path: string, o: Call = {}): Promise<Json> {
    const headers: Record<string, string> = { accept: "application/json, application/problem+json", authorization: `Bearer ${this.accessToken}` };
    let body: string | Uint8Array | undefined;
    if (o.raw !== undefined) {
      body = o.raw;
      headers["content-type"] = "application/octet-stream";
    } else if (o.body !== undefined) {
      body = JSON.stringify(o.body);
      headers["content-type"] = "application/json";
    }
    if (o.idempotencyKey !== undefined) headers["idempotency-key"] = o.idempotencyKey;
    Object.assign(headers, o.headers ?? {});
    const res = await this.fetcher(this.base + path, { method, headers, body });
    let doc: Json = {};
    try {
      const raw = await res.text();
      doc = raw ? (JSON.parse(raw) as Json) : {};
    } catch {
      doc = {};
    }
    if (res.status >= 400) {
      throw new V2Error(String(doc.code ?? "http_error"), res.status, String(doc.detail ?? ""), (doc.trace_id as string | undefined) ?? null, Boolean(doc.retryable));
    }
    return doc;
  }

  // discovery
  capabilities(): Promise<Json> {
    return this.call("GET", "/v2/capabilities");
  }
  coverage(country: string, profession?: string): Promise<Json> {
    return this.call("GET", `/v2/coverage?country=${country}${profession ? `&profession=${profession}` : ""}`);
  }

  /** The other ways to complete a check instead of a selfie or a photographed document. No reason is needed; choosing one is not a failed attempt. */
  captureAlternatives(country: string, reason?: string): Promise<Json> {
    return this.call("GET", `/v2/capture-alternatives?country=${country}${reason ? `&reason=${reason}` : ""}`);
  }

  // credentials
  verifyCredential(
    credentialType: string,
    issuerClaim: Json,
    credentialClaim: Json,
    jurisdiction: Record<string, string>,
    idempotencyKey: string,
    opts: Json = {},
  ): Promise<Json> {
    return this.call("POST", "/v2/credentials/verify", {
      body: { credential_type: credentialType, issuer_claim: issuerClaim, credential_claim: credentialClaim, jurisdiction, ...opts },
      idempotencyKey,
    });
  }
  credential(ref: string): Promise<Json> {
    return this.call("GET", `/v2/credentials/${ref}`);
  }

  // eligibility
  evaluateEligibility(jurisdiction: Record<string, string>, professionCode: string, serviceCode: string, idempotencyKey: string, subjectRef?: string): Promise<Json> {
    const body: Json = { jurisdiction, profession_code: professionCode, service_code: serviceCode };
    if (subjectRef) body.subject_ref = subjectRef;
    return this.call("POST", "/v2/eligibility/evaluate", { body, idempotencyKey });
  }
  eligibility(ref: string): Promise<Json> {
    return this.call("GET", `/v2/eligibility/${ref}`);
  }
  /** Zoikorum engagement eligibility per service, signed for Zoikorum: verify the `signature` offline with `verifyDecision`. */
  engagementCheck(
    professionalSubjectRef: string,
    engagementRef: string,
    professionCode: string,
    requestedServices: string[],
    jurisdiction: Record<string, string>,
    idempotencyKey: string,
    opts: Json = {},
  ): Promise<Json> {
    return this.call("POST", "/v2/zoikorum/engagement-checks", {
      body: {
        professional_subject_ref: professionalSubjectRef,
        engagement_ref: engagementRef,
        profession_code: professionCode,
        requested_services: requestedServices,
        jurisdiction,
        ...opts,
      },
      idempotencyKey,
    });
  }

  // cases
  openCase(purpose: string, jurisdiction: Record<string, string>, workflow: Record<string, string>, requestedChecks: string[], idempotencyKey: string, opts: Json = {}): Promise<Json> {
    return this.call("POST", "/v2/verifications", {
      body: { purpose, jurisdiction, workflow, requested_checks: requestedChecks, ...opts },
      idempotencyKey,
    });
  }
  case(caseId: string): Promise<Json> {
    return this.call("GET", `/v2/verifications/${caseId}`);
  }
  submitTask(caseId: string, check: string, body: Json, idempotencyKey: string): Promise<Json> {
    return this.call("POST", `/v2/verifications/${caseId}/tasks/${check}`, { body, idempotencyKey });
  }
  cancelCase(caseId: string): Promise<Json> {
    return this.call("POST", `/v2/verifications/${caseId}/cancel`);
  }

  // evidence
  /** Announce, then send the bytes with the short-lived token. Size and SHA-256 are computed here so they match exactly. A rejected
   * upload throws (for example `evidence_rejected_infected`). Returns the manifest of the stored upload. */
  async uploadEvidence(content: Uint8Array, declaredType: string, idempotencyKey: string, subjectRef?: string): Promise<Json> {
    const announce: Json = { declared_type: declaredType, size_bytes: content.length, sha256: await sha256Hex(content) };
    if (subjectRef) announce.subject_ref = subjectRef;
    const made = await this.call("POST", "/v2/evidence/uploads", { body: announce, idempotencyKey });
    // PUT /v2/evidence/uploads/{id}/content
    return this.call("PUT", String(made.upload_url), { raw: content, headers: { "x-upload-token": String(made.upload_token) } });
  }
  evidenceManifest(ref: string): Promise<Json> {
    return this.call("GET", `/v2/evidence/${ref}/manifest`);
  }
  deleteEvidence(ref: string): Promise<Json> {
    return this.call("DELETE", `/v2/evidence/${ref}`);
  }
  /** Exactly one of `evidenceRef` (a stored upload) and `content` (checked in memory and discarded). */
  validateDocument(declaredType: string, checks: string[], source: { evidenceRef: string } | { content: Uint8Array }, opts: Json = {}): Promise<Json> {
    const body: Json = { declared_type: declaredType, checks, ...opts };
    if ("evidenceRef" in source) body.evidence_ref = source.evidenceRef;
    else body.content_base64 = toBase64(source.content);
    return this.call("POST", "/v2/documents/validate", { body });
  }
  documentResult(ref: string): Promise<Json> {
    return this.call("GET", `/v2/documents/${ref}`);
  }

  // issuers, presentations, events
  /** The returned `nonce` is shown ONCE: keep it, a replay of this call will not return it again. */
  openIssuerCheck(issuerRef: string, credentialType: string, idempotencyKey: string, days?: number): Promise<Json> {
    const body: Json = { issuer_ref: issuerRef, credential_type: credentialType };
    if (days !== undefined) body.days = days;
    return this.call("POST", "/v2/issuer-checks", { body, idempotencyKey });
  }
  issuerCheck(ref: string): Promise<Json> {
    return this.call("GET", `/v2/issuer-checks/${ref}`);
  }
  /** `expiresInSeconds` can only SHORTEN the request's life (at most 600). */
  requestPresentation(
    configurationId: string,
    recipientRef: string,
    purpose: string,
    requestedClaims: string[],
    jurisdiction: Record<string, string>,
    idempotencyKey: string,
    expiresInSeconds?: number,
    opts: Json = {},
  ): Promise<Json> {
    const body: Json = { configuration_id: configurationId, recipient_ref: recipientRef, purpose, requested_claims: requestedClaims, jurisdiction, ...opts };
    if (expiresInSeconds !== undefined) body.expires_in_seconds = expiresInSeconds;
    return this.call("POST", "/v2/presentations/requests", { body, idempotencyKey });
  }
  presentation(ref: string): Promise<Json> {
    return this.call("GET", `/v2/presentations/${ref}`);
  }
  registerWebhook(url: string, eventTypes: string[], idempotencyKey: string, maxInflight?: number): Promise<Json> {
    const body: Json = { url, event_types: eventTypes };
    if (maxInflight !== undefined) body.max_inflight = maxInflight;
    return this.call("POST", "/v2/webhook-endpoints", { body, idempotencyKey });
  }
  webhookEndpoints(): Promise<Json> {
    return this.call("GET", "/v2/webhook-endpoints");
  }
  event(id: string): Promise<Json> {
    return this.call("GET", `/v2/events/${id}`);
  }
  replayEvent(id: string): Promise<Json> {
    return this.call("POST", `/v2/events/${id}/replay`);
  }
}

/** Every route this client calls, kept in step with the Python client and checked by a test. */
export const V2_PATHS = [
  "GET /v2/capabilities",
  "GET /v2/coverage",
  "GET /v2/capture-alternatives",
  "POST /v2/credentials/verify",
  "GET /v2/credentials/{ref}",
  "POST /v2/eligibility/evaluate",
  "GET /v2/eligibility/{ref}",
  "POST /v2/zoikorum/engagement-checks",
  "POST /v2/verifications",
  "GET /v2/verifications/{id}",
  "POST /v2/verifications/{id}/tasks/{check}",
  "POST /v2/verifications/{id}/cancel",
  "POST /v2/evidence/uploads",
  "PUT /v2/evidence/uploads/{id}/content",
  "GET /v2/evidence/{ref}/manifest",
  "DELETE /v2/evidence/{ref}",
  "POST /v2/documents/validate",
  "GET /v2/documents/{ref}",
  "POST /v2/issuer-checks",
  "GET /v2/issuer-checks/{ref}",
  "POST /v2/presentations/requests",
  "GET /v2/presentations/{ref}",
  "POST /v2/webhook-endpoints",
  "GET /v2/webhook-endpoints",
  "GET /v2/events/{id}",
  "POST /v2/events/{id}/replay",
] as const;
