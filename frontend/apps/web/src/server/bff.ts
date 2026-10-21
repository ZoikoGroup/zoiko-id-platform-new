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

/** Cell paths the browser may reach with the session's bearer token (via /api/bff). */
const API_PATH_PREFIXES = [
  "/oauth2/userinfo",
  "/oauth2/consents",
  "/sessions",
  "/authn/authenticators",
  "/authn/recovery-codes",
  "/authn/recovery/",
  "/admin/principals$",
  "/admin/principals/",
  "/admin/recovery-cases",
  "/admin/recovery-policies/",
  "/federation/integrations",
  "/federation/trust-anchors",
  "/verification-profiles",
  "/verification-adapters",
  "/verifications",
  "/principals/",
  "/verification-assertions/",
  "/verification-reliance-decisions",
  "/verification-review-cases/",
  "/credential-configurations",
  "/credential-issuer-trust",
  "/relying-party-registrations",
  "/credentials$",
  "/credentials/",
  "/presentations",
  "/v1/authority-grants",
  "/v1/approval-requests",
  "/v1/mandates$",
  "/v1/mandates/",
  "/v1/mandate-reservations/",
  "/access/v1/evaluation",
  "/security-state/",
  "/signals",
  "/ssf/streams",
  "/provisioning/sources",
  // Phase F: evidence, privacy, customer lockbox, residency, workload identity
  "/v1/evidence/",
  "/v1/privacy/",
  "/v1/lockbox-requests",
  "/v1/operator-access-policy$",
  "/v1/cells$",
  "/v1/tenant/",
  "/v1/machine/",
];

/** Browser-facing cell flows relayed on this origin (cell session cookie + Origin check). */
const PASSTHROUGH_PREFIXES = [
  "/authn/webauthn/",
  "/authn/step-up",
  "/authn/recovery$",
  "/authn/recovery/",
  "/oauth2/authorize",
  "/oauth2/consent/",
  "/federation/connections/",
  "/federation/callback",
  "/federation/saml/",
  "/.well-known/webauthn",
];

type Globals = { __zidBff?: { bff: Bff; env: RuntimeEnv } };

function sessionStore(): SessionStore {
  const url = process.env.ZID_VALKEY_URL;
  if (url) {
    return new ValkeySessionStore(new Valkey(url, { lazyConnect: false, maxRetriesPerRequest: 2 }), "zid:web");
  }
  if (process.env.NODE_ENV === "production" && process.env.ZID_ALLOW_MEMORY_SESSIONS !== "true") {
    throw new Error("ZID_VALKEY_URL is required in production: sessions must survive restarts and be shared by instances.");
  }
  return new MemorySessionStore();
}

/** One BFF per server process (kept on globalThis so dev hot-reload does not drop sessions). */
export function server(): { bff: Bff; env: RuntimeEnv } {
  const g = globalThis as Globals;
  if (g.__zidBff) return g.__zidBff;
  const env = readRuntimeEnv();
  const secure = env.publicOrigin.startsWith("https://") || env.publicOrigin.startsWith("http://localhost");
  const bff = createBff({
    app: "web",
    cellApiUrl: env.cellApiUrl,
    issuer: env.issuer,
    clientId: env.clientId,
    publicOrigin: env.publicOrigin,
    callbackPath: "/auth/callback",
    redirectUri: env.redirectUri,
    // Verification and presentation endpoints check these scopes; the first-party client holds them.
    scopes: ["openid", "verification:read", "verification:execute", "presentation:request"],
    secureCookies: secure,
    sessionTtlSeconds: 8 * 60 * 60,
    apiPathPrefixes: API_PATH_PREFIXES,
    passthroughPrefixes: PASSTHROUGH_PREFIXES,
    store: sessionStore(),
  });
  g.__zidBff = { bff, env };
  return g.__zidBff;
}
