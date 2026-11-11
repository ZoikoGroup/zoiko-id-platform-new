/** Backend-for-frontend for the Zoiko iD web app.
 *
 * The cell API has no CORS and authenticates browsers with its own `__Host-zid_session` cookie
 * (set by passkey / federation sign-in). So the web app serves everything from one origin:
 *
 * 1. `passthrough` — browser-facing flows (`/authn/*`, `/oauth2/authorize|consent`, `/federation/*`)
 *    are relayed verbatim to the cell. Only the cell's session cookie crosses; the app's own cookie
 *    never reaches the cell.
 * 2. `reauthorize` — turns the cell session cookie into tokens server-side (authorization code +
 *    PKCE, no page redirect). Used after sign-in and after step-up, so on-screen drafts survive.
 * 3. `proxy` — token-bearing API calls. Tokens stay in the server-side session; the browser holds an
 *    opaque, httpOnly cookie id plus a CSRF token.
 *
 * Nothing here decides authorization: the cell re-authorizes every call (protected actions, step-up,
 * approvals) and its answers are passed through unchanged. */
import { createHash } from "node:crypto";

import { applyDevice, resolveDevice } from "./device";
import { clearCookie, readCookie, serializeCookie, sessionCookieName } from "./cookies";
import { randomToken, type ConsoleSession, type SessionStore } from "./session";

export const CELL_SESSION_COOKIE = "__Host-zid_session";

export type BffConfig = {
  /** Short app name, used in the app cookie name (`__Host-zid_<app>`). */
  app: string;
  /** Cell API base URL, reached from the server only. */
  cellApiUrl: string;
  /** Token issuer (`iss`), i.e. the cell's ZID_TOKEN_ISSUER. */
  issuer: string;
  /** Default OAuth client (the app's own workspace). */
  clientId: string;
  /** Cookie naming another workspace's first-party console client (multi-company sign-up). The
   * cookie only chooses which tenant the user signs in to: the cell maps the client to its tenant
   * and the user must still authenticate there, so it never grants anything by itself. */
  workspaceCookie?: string;
  /** This app's public origin, e.g. https://id.acme.zoiko.com (sent as Origin on server-side calls). */
  publicOrigin: string;
  /** Path of this app's OAuth redirect endpoint. */
  callbackPath: string;
  /** Registered redirect URI when it differs from `${publicOrigin}${callbackPath}`. */
  redirectUri?: string;
  scopes: string[];
  secureCookies: boolean;
  /** First-party device cookie (fraud and abuse signals). On unless false. */
  deviceCookie?: boolean;
  sessionTtlSeconds: number;
  /** Upstream path prefixes reachable through `proxy` (a trailing "$" means exact match). */
  apiPathPrefixes: string[];
  /** Upstream path prefixes relayed by `passthrough`. */
  passthroughPrefixes: string[];
  store: SessionStore;
  /** Adds client authentication to token requests (e.g. a private_key_jwt assertion). */
  clientAuth?: (body: URLSearchParams) => Promise<void> | void;
  fetchImpl?: typeof fetch;
};

// sec-gpc: Global Privacy Control, an opt-out signal the cell must honor without verification (PRIV-21).
const FORWARD_REQUEST_HEADERS = ["accept", "accept-language", "content-type", "idempotency-key", "if-match", "if-none-match", "sec-gpc", "x-proof-kind", "x-proof-name"];
const FORWARD_RESPONSE_HEADERS = ["content-type", "etag", "retry-after", "x-correlation-id", "x-request-id", "www-authenticate"];
/** Prefix match, or exact match when the entry ends with "$". */
function allowed(list: string[], path: string): boolean {
  return list.some((p) => (p.endsWith("$") ? path === p.slice(0, -1) : path.startsWith(p)));
}

const NO_STORE = { "cache-control": "no-store, max-age=0", pragma: "no-cache" };

function json(body: unknown, status = 200, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json", ...NO_STORE, ...headers } });
}

/** Error in the cell's own envelope shape, so the UI parses one format. */
function bffError(status: number, code: string, message: string, correlationId: string): Response {
  return json({ success: false, error: { code, message, details: [] }, correlation_id: correlationId }, status, {
    "x-correlation-id": correlationId,
  });
}

/** Only same-origin relative paths; blocks `//evil.example` and backslash tricks. */
export function safeReturnTo(value: string | null | undefined, fallback = "/"): string {
  // Browsers treat "\" like "/", so any backslash or control character is refused outright.
  if (!value || !value.startsWith("/") || value.startsWith("//") || /[\\\x00-\x1f]/.test(value)) return fallback;
  return value;
}

function pkceChallenge(verifier: string): string {
  return createHash("sha256").update(verifier).digest("base64url");
}

function decodeJwtPayload(token: string): Record<string, unknown> {
  const part = token.split(".")[1];
  if (!part) throw new Error("malformed id_token");
  return JSON.parse(Buffer.from(part, "base64url").toString("utf8")) as Record<string, unknown>;
}

/** Only the cell session cookie goes upstream. */
function cellCookieHeader(req: Request): string | null {
  const v = readCookie(req.headers.get("cookie"), CELL_SESSION_COOKIE);
  return v ? `${CELL_SESSION_COOKIE}=${v}` : null;
}

function setCookieHeaders(res: Response): string[] {
  const h = res.headers as Headers & { getSetCookie?: () => string[] };
  return h.getSetCookie ? h.getSetCookie() : (res.headers.get("set-cookie")?.split(/,(?=\s*[^;,]+=)/) ?? []);
}

class AuthFailure extends Error {
  constructor(readonly reason: string) {
    super(reason);
  }
}

const CLIENT_ID = /^[A-Za-z0-9._:-]{3,128}$/;

/** The workspace client from a cookie header, or the default client. */
export function workspaceClient(cookieHeader: string | null, cookieName: string | undefined, fallback: string): string {
  const v = cookieName ? readCookie(cookieHeader, cookieName) : null;
  return v && CLIENT_ID.test(v) ? v : fallback;
}

export function createBff(config: BffConfig) {
  const doFetch = config.fetchImpl ?? fetch;
  const upstream = config.cellApiUrl.replace(/\/$/, "");
  const issuer = config.issuer.replace(/\/$/, "");
  const publicOrigin = config.publicOrigin.replace(/\/$/, "");
  const redirectUri = config.redirectUri ?? `${publicOrigin}${config.callbackPath}`;
  const cookieName = sessionCookieName(config.app, config.secureCookies);
  const clientFor = (req: Request) => workspaceClient(req.headers.get("cookie"), config.workspaceCookie, config.clientId);

  async function loadSession(req: Request): Promise<ConsoleSession | null> {
    const id = readCookie(req.headers.get("cookie"), cookieName);
    return id ? config.store.get(id) : null;
  }

  async function tokenRequest(body: URLSearchParams, clientId: string) {
    body.set("client_id", clientId);
    await config.clientAuth?.(body);
    const res = await doFetch(`${upstream}/oauth2/token`, {
      method: "POST",
      headers: { "content-type": "application/x-www-form-urlencoded", accept: "application/json" },
      body,
      cache: "no-store",
    });
    if (!res.ok) throw new Error(`token endpoint answered ${res.status}`);
    return (await res.json()) as { access_token: string; refresh_token?: string; id_token?: string; expires_in?: number };
  }

  /** Builds the authorize query for a fresh PKCE login and remembers the verifier. */
  async function beginAuthorization(returnTo: string, clientId: string) {
    const state = randomToken();
    const nonce = randomToken();
    const codeVerifier = randomToken(48);
    await config.store.putPending({ state, nonce, codeVerifier, returnTo: safeReturnTo(returnTo), createdAt: Date.now(), clientId }, 600);
    const q = new URLSearchParams({
      response_type: "code",
      client_id: clientId,
      redirect_uri: redirectUri,
      scope: config.scopes.join(" "),
      state,
      nonce,
      code_challenge: pkceChallenge(codeVerifier),
      code_challenge_method: "S256",
    });
    return `/oauth2/authorize?${q.toString()}`;
  }

  /** Exchanges a code and creates (or replaces) the app session. */
  async function completeAuthorization(state: string, code: string, previous: ConsoleSession | null) {
    const pending = await config.store.takePending(state);
    if (!pending) throw new AuthFailure("login_expired");
    const clientId = pending.clientId ?? config.clientId;
    let tokens;
    try {
      tokens = await tokenRequest(
        new URLSearchParams({ grant_type: "authorization_code", code, redirect_uri: redirectUri, code_verifier: pending.codeVerifier }),
        clientId,
      );
    } catch {
      throw new AuthFailure("token_exchange_failed");
    }
    // The id_token arrives over TLS straight from the token endpoint (OIDC Core §3.1.3.7 item 6);
    // its claims are checked and used for display only — never for authorization.
    let claims: Record<string, unknown> = {};
    if (tokens.id_token) {
      try {
        claims = decodeJwtPayload(tokens.id_token);
      } catch {
        throw new AuthFailure("invalid_id_token");
      }
      const aud = claims.aud;
      const audOk = Array.isArray(aud) ? aud.includes(clientId) : aud === clientId;
      if (claims.nonce !== pending.nonce || !audOk || String(claims.iss ?? "").replace(/\/$/, "") !== issuer) {
        throw new AuthFailure("invalid_id_token");
      }
    }
    if (previous) await config.store.delete(previous.id);
    const now = Math.floor(Date.now() / 1000);
    const session: ConsoleSession = {
      id: randomToken(),
      csrf: previous?.csrf ?? randomToken(),
      accessToken: tokens.access_token,
      refreshToken: tokens.refresh_token,
      idToken: tokens.id_token,
      accessTokenExpiresAt: now + (tokens.expires_in ?? 300),
      createdAt: now,
      clientId,
      subject: {
        sub: String(claims.sub ?? "unknown"),
        tenantId: typeof claims.tenant_id === "string" ? claims.tenant_id : undefined,
        acr: typeof claims.acr === "string" ? claims.acr : undefined,
        authTime: typeof claims.auth_time === "number" ? claims.auth_time : undefined,
      },
    };
    await config.store.set(session, config.sessionTtlSeconds);
    return { session, returnTo: pending.returnTo };
  }

  /** Server-side authorize using the browser's cell session cookie (no page redirect). */
  async function authorizeWithCellCookie(cellCookie: string, previous: ConsoleSession | null, clientId: string) {
    const path = await beginAuthorization("/", clientId);
    const res = await doFetch(`${upstream}${path}`, {
      method: "GET",
      headers: { cookie: cellCookie, accept: "application/json" },
      redirect: "manual",
      cache: "no-store",
    });
    const location = res.headers.get("location");
    if (!location) throw new AuthFailure(res.status === 503 ? "temporarily_unavailable" : "authorize_failed");
    const params = new URL(location, publicOrigin).searchParams;
    const error = params.get("error");
    if (error) throw new AuthFailure(error);
    const code = params.get("code");
    const state = params.get("state");
    if (!code || !state) throw new AuthFailure("authorize_failed");
    return completeAuthorization(state, code, previous);
  }

  async function freshSession(req: Request, session: ConsoleSession): Promise<ConsoleSession | null> {
    const now = Math.floor(Date.now() / 1000);
    if (session.accessTokenExpiresAt - 20 > now) return session;
    if (session.refreshToken) {
      try {
        const t = await tokenRequest(
          new URLSearchParams({ grant_type: "refresh_token", refresh_token: session.refreshToken }),
          session.clientId ?? config.clientId,
        );
        const next: ConsoleSession = {
          ...session,
          accessToken: t.access_token,
          refreshToken: t.refresh_token ?? session.refreshToken,
          accessTokenExpiresAt: now + (t.expires_in ?? 300),
        };
        await config.store.set(next, config.sessionTtlSeconds);
        return next;
      } catch {
        /* fall through to the cell cookie */
      }
    }
    const cellCookie = cellCookieHeader(req);
    if (!cellCookie) return null;
    try {
      // Re-saved under the existing app session id so the browser cookie stays valid.
      const { session: renewed } = await authorizeWithCellCookie(cellCookie, null, session.clientId ?? clientFor(req));
      await config.store.delete(renewed.id);
      const merged: ConsoleSession = { ...renewed, id: session.id, csrf: session.csrf };
      await config.store.set(merged, config.sessionTtlSeconds);
      return merged;
    } catch {
      return null;
    }
  }

  const deviceFor = (req: Request) => resolveDevice(req.headers.get("cookie"), config.secureCookies, config.deviceCookie !== false);

  const sessionCookie = (session: ConsoleSession) =>
    serializeCookie(cookieName, session.id, config.sessionTtlSeconds, config.secureCookies);

  return {
    /** Relative authorize URL for redirect-based flows (federation `return_to`). */
    authorizePath(returnTo: string, clientId?: string): Promise<string> {
      return beginAuthorization(returnTo, clientId ?? config.clientId);
    },

    /** The device id for a browser request: reused from its cookie, or minted (with the Set-Cookie to send). */
    device: deviceFor,

    /** The workspace client this request signs in through (cookie, else the default). */
    clientFor(cookieHeader: string | null): string {
      return workspaceClient(cookieHeader, config.workspaceCookie, config.clientId);
    },

    /** POST /api/auth/session — after passkey sign-in or step-up: cell cookie → app session. */
    async reauthorize(req: Request): Promise<Response> {
      const correlationId = randomToken(12);
      const origin = req.headers.get("origin");
      if (origin && origin !== publicOrigin) return bffError(403, "CSRF_REJECTED", "The request could not be verified.", correlationId);
      const cellCookie = cellCookieHeader(req);
      if (!cellCookie) return bffError(401, "LOGIN_REQUIRED", "Sign in to continue.", correlationId);
      const previous = await loadSession(req);
      try {
        const { session } = await authorizeWithCellCookie(cellCookie, previous, clientFor(req));
        return json({ authenticated: true, subject: session.subject, csrf: session.csrf }, 200, { "set-cookie": sessionCookie(session) });
      } catch (e) {
        const reason = e instanceof AuthFailure ? e.reason : "authorize_failed";
        const status = reason === "login_required" ? 401 : reason === "temporarily_unavailable" ? 503 : 400;
        return bffError(status, reason.toUpperCase(), "Sign-in could not be completed.", correlationId);
      }
    },

    /** GET /auth/callback — redirect-based completion (federation sign-in). */
    async callback(req: Request): Promise<Response> {
      const url = new URL(req.url);
      const fail = (reason: string) =>
        new Response(null, { status: 303, headers: { location: `/sign-in?error=${encodeURIComponent(reason)}`, ...NO_STORE } });
      if (url.searchParams.get("error")) return fail(url.searchParams.get("error") ?? "access_denied");
      const state = url.searchParams.get("state");
      const code = url.searchParams.get("code");
      if (!state || !code) return fail("invalid_request");
      try {
        const { session, returnTo } = await completeAuthorization(state, code, await loadSession(req));
        return new Response(null, { status: 303, headers: { location: returnTo, "set-cookie": sessionCookie(session), ...NO_STORE } });
      } catch (e) {
        return fail(e instanceof AuthFailure ? e.reason : "authorize_failed");
      }
    },

    /** POST /api/auth/logout — ends the cell session and the app session; clears client state. */
    async logout(req: Request): Promise<Response> {
      const session = await loadSession(req);
      const correlationId = randomToken(12);
      if (session && req.headers.get("x-zid-csrf") !== session.csrf) {
        return bffError(403, "CSRF_REJECTED", "The request could not be verified.", correlationId);
      }
      const headers = new Headers({ ...NO_STORE, "content-type": "application/json", "clear-site-data": '"cache", "storage"' });
      const cellCookie = cellCookieHeader(req);
      if (cellCookie) {
        await doFetch(`${upstream}/authn/logout`, {
          method: "POST",
          headers: { cookie: cellCookie, origin: publicOrigin, "content-type": "application/json" },
          body: JSON.stringify({ client_id: session?.clientId ?? clientFor(req) }),
        }).catch(() => null);
      }
      if (session?.refreshToken) {
        const body = new URLSearchParams({
          token: session.refreshToken,
          token_type_hint: "refresh_token",
          client_id: session.clientId ?? config.clientId,
        });
        await config.clientAuth?.(body);
        await doFetch(`${upstream}/oauth2/revoke`, {
          method: "POST",
          headers: { "content-type": "application/x-www-form-urlencoded" },
          body,
        }).catch(() => null);
      }
      if (session) await config.store.delete(session.id);
      headers.append("set-cookie", clearCookie(cookieName, config.secureCookies));
      headers.append("set-cookie", `${CELL_SESSION_COOKIE}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0; Secure`);
      return new Response(JSON.stringify({ signedOut: true }), { status: 200, headers });
    },

    /** GET /api/auth/session — display identity + CSRF token. Never tokens. `apiBase` is the public API address (the token issuer): not secret, and what an
     * external caller such as a build pipeline is pointed at. */
    async session(req: Request): Promise<Response> {
      const stored = await loadSession(req);
      const session = stored ? await freshSession(req, stored) : null;
      if (!session) return json({ authenticated: false });
      return json({ authenticated: true, subject: session.subject, csrf: session.csrf, apiBase: issuer });
    },

    /** Server components: the current display subject, or null. */
    async currentSubject(cookieHeader: string | null): Promise<ConsoleSession["subject"] | null> {
      const id = readCookie(cookieHeader, cookieName);
      const s = id ? await config.store.get(id) : null;
      return s?.subject ?? null;
    },

    /** Browser-facing cell flows, relayed verbatim on this origin. */
    async passthrough(req: Request, path: string): Promise<Response> {
      const correlationId = req.headers.get("x-correlation-id")?.slice(0, 64) || randomToken(12);
      if (!allowed(config.passthroughPrefixes, path) || path.includes("..")) {
        return bffError(404, "NOT_FOUND", "The requested resource was not found.", correlationId);
      }
      const headers = new Headers({ "x-correlation-id": correlationId });
      for (const h of [...FORWARD_REQUEST_HEADERS, "origin"]) {
        const v = req.headers.get(h);
        if (v) headers.set(h, v);
      }
      const cellCookie = cellCookieHeader(req);
      if (cellCookie) headers.set("cookie", cellCookie);
      const device = deviceFor(req);
      applyDevice(headers, device);
      const method = req.method.toUpperCase();
      let res: Response;
      try {
        res = await doFetch(`${upstream}${path}${new URL(req.url).search}`, {
          method,
          headers,
          body: ["GET", "HEAD"].includes(method) ? undefined : await req.arrayBuffer(),
          redirect: "manual",
          cache: "no-store",
        });
      } catch {
        return bffError(503, "SERVICE_UNAVAILABLE", "The service is temporarily unavailable.", correlationId);
      }
      const out = new Headers(NO_STORE);
      for (const h of [...FORWARD_RESPONSE_HEADERS, "location"]) {
        const v = res.headers.get(h);
        if (v) out.set(h, v);
      }
      for (const c of setCookieHeaders(res)) {
        if (c.trim().startsWith(`${CELL_SESSION_COOKIE}=`)) out.append("set-cookie", c);
      }
      if (device.setCookie) out.append("set-cookie", device.setCookie);
      return new Response(res.body, { status: res.status, headers: out });
    },

    /** Token-bearing API calls: /api/bff/<cell path>. */
    async proxy(req: Request, path: string[]): Promise<Response> {
      const correlationId = req.headers.get("x-correlation-id")?.slice(0, 64) || randomToken(12);
      if (path.some((p) => p === ".." || p === "." || p === "")) {
        return bffError(404, "NOT_FOUND", "The requested resource was not found.", correlationId);
      }
      // Segments carry the cell's `{id}:action` and `{tenant}.{ref}` syntax, so ':' stays literal.
      const target = "/" + path.map((p) => encodeURIComponent(p).replace(/%3A/gi, ":")).join("/");
      if (!allowed(config.apiPathPrefixes, target)) {
        return bffError(404, "NOT_FOUND", "The requested resource was not found.", correlationId);
      }
      const stored = await loadSession(req);
      const session = stored ? await freshSession(req, stored) : null;
      if (!session) return bffError(401, "LOGIN_REQUIRED", "Your session has ended. Sign in again to continue.", correlationId);

      const method = req.method.toUpperCase();
      if (!["GET", "HEAD"].includes(method)) {
        const origin = req.headers.get("origin");
        if (req.headers.get("x-zid-csrf") !== session.csrf || (origin && origin !== publicOrigin)) {
          return bffError(403, "CSRF_REJECTED", "The request could not be verified.", correlationId);
        }
      }
      const headers = new Headers({ authorization: `Bearer ${session.accessToken}`, "x-correlation-id": correlationId });
      for (const h of FORWARD_REQUEST_HEADERS) {
        const v = req.headers.get(h);
        if (v) headers.set(h, v);
      }
      const device = deviceFor(req);
      applyDevice(headers, device);
      let res: Response;
      try {
        res = await doFetch(`${upstream}${target}${new URL(req.url).search}`, {
          method,
          headers,
          body: ["GET", "HEAD"].includes(method) ? undefined : await req.arrayBuffer(),
          redirect: "manual",
          cache: "no-store",
        });
      } catch {
        return bffError(503, "SERVICE_UNAVAILABLE", "The service is temporarily unavailable.", correlationId);
      }
      const out = new Headers(NO_STORE);
      for (const h of FORWARD_RESPONSE_HEADERS) {
        const v = res.headers.get(h);
        if (v) out.set(h, v);
      }
      if (!out.has("x-correlation-id")) out.set("x-correlation-id", correlationId);
      if (device.setCookie) out.append("set-cookie", device.setCookie);
      return new Response(res.body, { status: res.status, headers: out });
    },
  };
}

export type Bff = ReturnType<typeof createBff>;
