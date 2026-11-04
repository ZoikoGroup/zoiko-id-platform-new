/** One error shape for the UI, whatever the cell surface returned.
 *
 * - Platform envelope: `{success:false, request_id, correlation_id, error:{code, message, details:[{field, issue}],
 *   retryable, retry_after_ms}}`.
 * - OAuth / credential / presentation endpoints: `{error, error_description}`.
 * - SCIM: `{schemas, status, detail, scimType}`.
 * Only the canonical code and the server's message are displayed; the correlation id is shown for support. */
export type FieldIssue = { field?: string; issue: string };

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details: FieldIssue[];
  readonly correlationId: string | undefined;
  readonly retryable: boolean;
  readonly retryAfterMs: number | undefined;
  /** `WWW-Authenticate` on STEP_UP_REQUIRED (carries the required acr_values). */
  readonly authenticate: string | undefined;

  constructor(init: {
    status: number;
    code: string;
    message: string;
    details?: FieldIssue[];
    correlationId?: string;
    retryable?: boolean;
    retryAfterMs?: number;
    authenticate?: string;
  }) {
    super(init.message);
    this.name = "ApiError";
    this.status = init.status;
    this.code = init.code;
    this.details = init.details ?? [];
    this.correlationId = init.correlationId;
    this.retryable = init.retryable ?? false;
    this.retryAfterMs = init.retryAfterMs;
    this.authenticate = init.authenticate;
  }

  get isStepUpRequired() {
    return this.code === "STEP_UP_REQUIRED";
  }
  get isApprovalRequired() {
    return this.code === "APPROVAL_REQUIRED";
  }
  get isLoginRequired() {
    return this.status === 401 || this.code === "LOGIN_REQUIRED" || this.code === "login_required";
  }
  get isForbidden() {
    return this.status === 403 && !this.isStepUpRequired && !this.isApprovalRequired;
  }
  get isNotFound() {
    return this.status === 404;
  }

  static async from(response: Response): Promise<ApiError> {
    let body: unknown = undefined;
    try {
      body = await response.clone().json();
    } catch {
      /* not JSON */
    }
    const retryAfter = response.headers.get("retry-after");
    return ApiError.fromBody(response.status, body, {
      correlationId: response.headers.get("x-correlation-id") ?? undefined,
      retryAfterMs: retryAfter && /^\d+$/.test(retryAfter) ? Number(retryAfter) * 1000 : undefined,
      authenticate: response.headers.get("www-authenticate") ?? undefined,
    });
  }

  static fromBody(
    status: number,
    body: unknown,
    meta: { correlationId?: string; retryAfterMs?: number; authenticate?: string } = {},
  ): ApiError {
    const b = (body && typeof body === "object" ? body : {}) as Record<string, unknown>;
    const correlationId = (typeof b.correlation_id === "string" ? b.correlation_id : undefined) ?? meta.correlationId;
    const common = { status, correlationId, authenticate: meta.authenticate };

    const err = b.error;
    if (err && typeof err === "object") {
      const e = err as { code?: string; message?: string; details?: FieldIssue[]; retryable?: boolean; retry_after_ms?: number };
      return new ApiError({
        ...common,
        code: e.code ?? "ERROR",
        message: e.message ?? "The request failed.",
        details: (e.details ?? []).filter((d) => d && d.issue),
        retryable: e.retryable,
        retryAfterMs: e.retry_after_ms ?? meta.retryAfterMs,
      });
    }
    if (typeof err === "string") {
      const desc = typeof b.error_description === "string" ? b.error_description : undefined;
      return new ApiError({ ...common, code: err, message: desc ?? humanizeCode(err), retryAfterMs: meta.retryAfterMs });
    }
    if (Array.isArray(b.schemas) && typeof b.detail === "string") {
      return new ApiError({ ...common, code: typeof b.scimType === "string" ? b.scimType : "SCIM_ERROR", message: b.detail });
    }
    return new ApiError({
      ...common,
      code: status >= 500 ? "SERVICE_UNAVAILABLE" : "ERROR",
      message: status >= 500 ? "The service is temporarily unavailable." : `Request failed (${status}).`,
      retryable: status >= 500,
      retryAfterMs: meta.retryAfterMs,
    });
  }
}

function humanizeCode(code: string): string {
  const s = code.replace(/_/g, " ");
  return s.charAt(0).toUpperCase() + s.slice(1) + ".";
}

/** What a central route guard asks a client to prove: the action, the resource and the exact change the server derived from the request
 * (the route, its parameters and a digest of the body). Null when the refusal is any other kind of step-up. */
export type RouteStepUp = { action: string; resource: string; change: Record<string, unknown> };

export function routeStepUp(e: unknown): RouteStepUp | null {
  if (!(e instanceof ApiError) || !e.isStepUpRequired) return null;
  const field = (name: string) => e.details.find((d) => d.field === name)?.issue;
  const action = field("action");
  const resource = field("resource");
  const change = field("change");
  if (field("binding") !== "route" || !action || !resource || !change) return null;
  try {
    const parsed: unknown = JSON.parse(change);
    return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? { action, resource, change: parsed as Record<string, unknown> } : null;
  } catch {
    return null;
  }
}
