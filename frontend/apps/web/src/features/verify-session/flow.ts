/** The hosted verification's logic (ZID-CP-WF01 sections 05, 06, 08, 10). The person has no account: the link's one-use token is spent once for a
 * session credential held in memory only (never in web storage, §29). The token arrives in the URL fragment, which no server ever sees, and is removed
 * from the address bar at once. To continue after leaving, the person asks the business for a new link (S12: the business's resume link). */

export const BASE = "/api/public/verify-session";

export type Method = { method: string; kind: string; needs_nfc: boolean; needs_camera: boolean; biometric: boolean; requires_handoff: boolean };
export type Action = { code: string; category: string; message: string };
export type Status = {
  session_ref: string;
  reference: string;
  state: string;
  acknowledged: boolean;
  biometric_consent: boolean;
  method: string | null;
  submitted: boolean;
  final: boolean;
  action: Action | null;
  expires_at: string | null;
  mode: "sandbox" | "production";
  return_url?: string;
  return_state?: string | null;
};
export type Context = {
  requester: { name: string | null; verified_by: string };
  purpose_code: string;
  market: string;
  notice: { notice_version: string; content_uri: string | null; acknowledged: boolean } | null;
  market_notice_version: string | null;
  lawful_basis: string | null;
  languages: string[];
  methods: Method[];
  methods_withheld: { method: string; reason: string }[];
  status: Status;
};
export type Credential = { ref: string; token: string; expires_at: string };
/** What the person types on the automatic route; compared once with the platform's own reading of the document and never stored. */
export type Declared = { surname: string; given_names: string; document_number: string; birth_date: string };

export function declaredComplete(d: Declared): boolean {
  return Boolean(d.surname.trim() && d.given_names.trim() && d.document_number.trim().length >= 3 && /^\d{4}-\d{2}-\d{2}$/.test(d.birth_date));
}

export class FlowError extends Error {
  constructor(
    readonly code: string,
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

/** The one-use token from `#t=...`, or null. */
export function launchToken(hash: string): string | null {
  const t = new URLSearchParams(hash.replace(/^#/, "")).get("t");
  return t && /^[0-9a-f]{32}\.[A-Za-z0-9_-]{32,}$/.test(t) ? t : null;
}

async function call<T>(path: string, init: RequestInit & { token?: string } = {}): Promise<T> {
  const headers: Record<string, string> = { accept: "application/json" };
  if (init.body) headers["content-type"] = "application/json";
  if (init.token) headers["x-zoiko-applicant-token"] = init.token;
  let res: Response;
  try {
    res = await fetch(`${BASE}/${path}`, { ...init, headers, cache: "no-store" });
  } catch {
    throw new FlowError("PROVIDER_TEMPORARILY_UNAVAILABLE", "This verification step is unavailable right now.", 0);
  }
  const body = (await res.json().catch(() => ({}))) as Record<string, unknown> & { data?: unknown };
  if (!res.ok) {
    // the cell answers an RFC 9457 problem ({code, detail}); the relay's own refusals carry {code, message}
    const err = (body.error ?? body) as { code?: string; detail?: string; message?: string };
    throw new FlowError((err.code ?? "ERROR").toUpperCase(), err.detail ?? err.message ?? "Something went wrong. Try again.", res.status);
  }
  return (body.data ?? body) as T;
}

export const api = {
  launch: (launch_token: string) =>
    call<{ session_ref: string; access_token: string; expires_at: string }>("launch", { method: "POST", body: JSON.stringify({ launch_token }) }),
  context: (c: Credential, device: { nfc: boolean; camera: boolean }) =>
    call<Context>(`${c.ref}/public-context?nfc=${device.nfc}&camera=${device.camera}`, { token: c.token }),
  status: (c: Credential) => call<Status>(`${c.ref}/public-status`, { token: c.token }),
  notices: (c: Credential, language: string, consent: boolean, biometric_consent: boolean) =>
    call<Status>(`${c.ref}/notices`, { method: "POST", token: c.token, body: JSON.stringify({ language, consent, biometric_consent }) }),
  select: (c: Credential, method: string, device: { nfc: boolean; camera: boolean }) =>
    call<Status>(`${c.ref}/method-select`, { method: "POST", token: c.token, body: JSON.stringify({ method, ...device }) }),
  intent: (c: Credential, step: "DOCUMENT" | "FACE" | "CHIP") =>
    call<{ nonce: string; expires_at: string }>(`${c.ref}/capture-intents`, { method: "POST", token: c.token, body: JSON.stringify({ step }) }),
  document: (
    c: Credential,
    body: { nonce: string; captured_at: string; image_base64: string; selfie_base64?: string; consent?: boolean; declared?: Declared },
  ) =>
    call<Status>(`${c.ref}/evidence/document-image`, { method: "POST", token: c.token, body: JSON.stringify(body) }),
  submit: (c: Credential) => call<Status>(`${c.ref}/submit`, { method: "POST", token: c.token }),
  handoff: (c: Credential) => call<{ handoff_url: string; expires_at: string }>(`${c.ref}/handoff`, { method: "POST", token: c.token }),  cancel: (c: Credential) => call<Status>(`${c.ref}/applicant-cancel`, { method: "POST", token: c.token }),
};

export type Screen = "welcome" | "choose" | "capture" | "review-submit" | "processing" | "finished" | "ended";

/** Which screen the person sees, from the server's state only: the page never infers a result (section 04). */
export function screenFor(s: Status): Screen {
  switch (s.state) {
    case "CREATED":
    case "NOTICE_PRESENTED":
      return s.acknowledged ? "choose" : "welcome";
    case "METHOD_SELECTED":
    case "RESUBMISSION_REQUESTED":
      return "capture";
    case "CAPTURING":
      return s.action?.category === "USER_CORRECTABLE" ? "capture" : "review-submit";
    case "MANUAL_REVIEW":
      return "processing";
    case "FINAL_APPROVED":
    case "FINAL_DECLINED":
    case "INCONCLUSIVE":
      return "finished";
    default:
      return "ended"; // CANCELED, EXPIRED
  }
}

/** "Step X of Y" from the person's own path (section 06). */
export function step(screen: Screen): { at: number; of: number } | null {
  const order: Screen[] = ["welcome", "choose", "capture", "review-submit"];
  const at = order.indexOf(screen);
  return at < 0 ? null : { at: at + 1, of: order.length };
}

/** Back to the business, only to the address the server returned (validated against the business's verified domains when the session opened). */
export function returnHref(s: Status): string | null {
  if (!s.return_url) return null;
  try {
    const u = new URL(s.return_url);
    u.searchParams.set("session_ref", s.session_ref);
    if (s.return_state) u.searchParams.set("state", s.return_state);
    return u.toString();
  } catch {
    return null;
  }
}
