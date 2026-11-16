import "server-only";

import {
  createBff,
  MemorySessionStore,
  readRuntimeEnv,
  ValkeySessionStore,
  type Bff,
  type RuntimeEnv,
  type SessionStore,
} from "@zoiko/auth-bff";
import Valkey from "iovalkey";

/** The Operator Console is its own origin, OIDC client and session store (DEC-F4 §5, DEC-H1): it
 * reaches only the operator plane. Customer administration lives in the web app, never here. */
const API_PATH_PREFIXES = ["/operator/", "/platform/", "/ops/", "/v1/commercial/", "/v1/regulatory/", "/v1/reviews/signals", "/v1/reviews/screening-cases/", "/v1/reviews/kyb-cases/"];

/** Passkey sign-in and step-up, and the operator email + password sign-in, on this origin. */
const PASSTHROUGH_PREFIXES = ["/authn/webauthn/", "/authn/operator/password", "/authn/step-up", "/oauth2/authorize", "/.well-known/webauthn"];

type Globals = { __zidOperatorBff?: { bff: Bff; env: RuntimeEnv } };

function sessionStore(): SessionStore {
  const url = process.env.ZID_VALKEY_URL;
  if (url) {
    return new ValkeySessionStore(new Valkey(url, { lazyConnect: false, maxRetriesPerRequest: 2 }), "zid:operator");
  }
  if (process.env.NODE_ENV === "production" && process.env.ZID_ALLOW_MEMORY_SESSIONS !== "true") {
    throw new Error("ZID_VALKEY_URL is required in production: sessions must survive restarts and be shared by instances.");
  }
  return new MemorySessionStore();
}

export function server(): { bff: Bff; env: RuntimeEnv } {
  const g = globalThis as Globals;
  if (g.__zidOperatorBff) return g.__zidOperatorBff;
  const env = readRuntimeEnv();
  const secure = env.publicOrigin.startsWith("https://") || env.publicOrigin.startsWith("http://localhost");
  const bff = createBff({
    app: "operator",
    cellApiUrl: env.cellApiUrl,
    issuer: env.issuer,
    clientId: env.clientId,
    publicOrigin: env.publicOrigin,
    callbackPath: "/auth/callback",
    redirectUri: env.redirectUri,
    scopes: ["openid"],
    secureCookies: secure,
    sessionTtlSeconds: 60 * 60, // short operator sessions; every action re-checks AAL-Z3 freshness
    apiPathPrefixes: API_PATH_PREFIXES,
    passthroughPrefixes: PASSTHROUGH_PREFIXES,
    store: sessionStore(),
  });
  g.__zidOperatorBff = { bff, env };
  return g.__zidOperatorBff;
}
