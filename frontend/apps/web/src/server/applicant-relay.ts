/** The hosted verification's same-origin relay (ZID-CP-WF01 §08). Public by design: the person has no Zoiko iD session, so nothing here carries a cookie or
 * a bearer token. Only the person's own session endpoints pass, only the session credential header is forwarded, and only the two query flags the method
 * list reads. Everything else is refused here, before the cell is asked. */

const REF = "vs_[0-9a-f-]{36}";
const RULES: ReadonlyArray<[string, RegExp]> = [
  ["POST", /^launch$/],
  ["GET", new RegExp(`^${REF}/(public-context|public-status)$`)],
  ["POST", new RegExp(`^${REF}/(notices|method-select|capture-intents|evidence/document-image|evidence/chip|evidence/nfc-state|submit|handoff|applicant-cancel)$`)],
];

export const TOKEN_HEADER = "x-zoiko-applicant-token";
/** A document and a live photograph, base64, with room for the JSON around them. */
export const MAX_BODY_BYTES = 24 * 1024 * 1024;

export function allowed(method: string, path: string): boolean {
  return RULES.some(([m, re]) => m === method && re.test(path));
}

export function query(search: URLSearchParams): string {
  const out = new URLSearchParams();
  for (const key of ["nfc", "camera"]) {
    const v = search.get(key);
    if (v === "true" || v === "false") out.set(key, v);
  }
  const s = out.toString();
  return s ? `?${s}` : "";
}

export async function relay(req: Request, path: string, cellApiUrl: string): Promise<Response> {
  if (!allowed(req.method, path)) return Response.json({ code: "NOT_FOUND", message: "Not found." }, { status: 404 });
  const headers: Record<string, string> = { accept: "application/json" };
  const token = req.headers.get(TOKEN_HEADER);
  if (token) headers[TOKEN_HEADER] = token;
  let body: string | undefined;
  if (req.method === "POST") {
    body = await req.text();
    if (body.length > MAX_BODY_BYTES) return Response.json({ code: "PAYLOAD_TOO_LARGE", message: "The photograph is too large." }, { status: 413 });
    headers["content-type"] = "application/json";
  }
  const url = `${cellApiUrl.replace(/\/$/, "")}/v2/verification-sessions/${path}${query(new URL(req.url).searchParams)}`;
  try {
    const res = await fetch(url, { method: req.method, headers, body, cache: "no-store" });
    return new Response(await res.text(), {
      status: res.status,
      headers: { "content-type": res.headers.get("content-type") ?? "application/json", "cache-control": "no-store" },
    });
  } catch {
    return Response.json({ code: "PROVIDER_TEMPORARILY_UNAVAILABLE", message: "This verification step is unavailable right now." }, { status: 503 });
  }
}
