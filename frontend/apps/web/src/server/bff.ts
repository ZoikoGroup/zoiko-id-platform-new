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
  "/v1/policy-releases$",
  "/v1/policy-releases/",
  "/dev/verification-simulator/",
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
  // Supplier spend budget, usage counts and the face engine readiness view (WF01-011/012/014). The cell decides each call by scope and action.
  "/verification-budget",
  "/verification-usage",
  "/verification-engine-readiness",
  "/verification-method-certification",
  "/v2/age-checks",
  // A company's verification grade, entitlements and representatives' mandates (WF02, CR-109). The cell decides each call by action.
  "/v1/kyb/",
  // Identity proofing cases (WF03, CR-110). The cell decides each call by scope and action.
  "/v1/idv/",
  // The Command Center (ZID-CPS-WF-02): aggregates under the analytics permission bundles, cost and price under billing access. The cell decides each call.
  "/v1/verify-analytics/",
  // Screening requests for a tenant (WF06 S01 to S03, CR-113). The cell requires an OWNER or ADMIN and a signed market policy; the operator routes are not reachable from here.
  "/v1/screening/eligibility",
  "/v1/screening/cases",
  "/v1/screening/ownership/rollup",
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
  "/v1/credentials/",
  "/v2/evidence/",
  // Source of funds and wealth: the review queue, a case, the decision and the appeal (CR-096). The cell decides each call by role and line state.
  "/v2/source-of-funds/",
  "/v2/ownership-graphs",
  // The governed country-signing-CA trust store (WF01 screen O2, CR-107). The cell decides each call by action and by who staged the package.
  "/v2/trust-store/",
  // Verification workflows: drafts, the safety analysis, simulation, approval and runs (CR-099). The cell decides each call by role.
  "/v2/workflows",
  "/v2/workflow-runs",
  "/v2/documents/",
  "/v2/assurance/",
  "/presentations",
  "/v1/authority-grants",
  "/v1/delegation-templates",
  "/v1/access-requests",
  "/v1/service-requests",
  "/v1/insights/",
  "/v1/approval-requests",
  "/v1/mandates$",
  "/v1/mandates/",
  "/v1/mandate-reservations/",
  "/access/v1/evaluation",
  "/security-state/",
  "/signals",
  "/ssf/streams",
  "/provisioning/sources",
  "/provisioning/directory-syncs",
  // Phase F: evidence, privacy, customer lockbox, residency, workload identity
  "/v1/evidence/",
  "/v1/routing/", // CR-171 provider coverage, route decisions, attempts and releases (the back-office console; applicants never call these)
  "/v1/trust/issuer$", // the issuer's signed trust bundle, shown in the trust inspector (public on the cell, exact path only)
  "/v1/privacy/",
  "/v1/lockbox-requests",
  "/v1/operator-access-policy$",
  "/v1/cells$",
  "/v1/tenant$",
  "/v1/tenant/",
  "/v1/tenant:suspend",
  "/v1/tenant:resume",
  "/v1/tenant:offboard",
  "/v1/onboarding/",
  "/v1/integration/",
  "/v1/machine/",
  // The Delegated Authority & Access Readiness agent screens (CR-091). The cell answers 404 for every route while the feature is off.
  "/v1/access-readiness/",
  "/v1/webhooks/",
  "/v1/alerts",
  "/v1/search",
  "/v1/settings",
  "/v1/usage",
  // Phase L: the organization pages (setup, approvals, decisions), step-up proofs, company claims and plan features. Each is a route a
  // console screen calls; the cell still decides every call by role, authority, step-up and approvals.
  "/v1/organizations/",
  "/v1/approval-cases",
  "/v1/approval-policy",
  "/v1/governance/",
  "/v1/step-up/",
  "/v1/org-claims",
  "/v1/entitlements",
  // The team page's role gallery (the published matrix) and the verification level card (ZID-UX-COM-010 C00, ZID-EDR-006).
  "/v1/roles/",
  "/v1/verification/",
  // WF-05 verification checks (CR-112 to CR-118): cases, contacts and codes, address and documents, method eligibility, the company policy. The cell
  // decides each call by scope and role.
  "/v1/verification-cases",
  "/v1/contacts/",
  "/v1/check-methods/",
  "/v1/check-policies",
  // Billing (ZID-COM-001): the tenant's own plan, ledger, invoices and budgets. The public pricing page reads the
  // catalogue server-side without a session; the console's Subscription tab reads the same read-only catalogue here
  // so a company can see the plans on offer and ask for one.
  "/v1/catalog$",
  "/v1/tenants/",
  "/v1/billing/",
  "/v1/account/",
  "/v1/invoices",
  "/v1/budgets",
  // The solution builder (ZID-COM-WF-2026-11 B06): offers by market, service dependencies, quote preview and saved drafts. Named one by one: the
  // operator routes under /v1/commercial/ (market matrices, quote requests) are never relayed from the browser.
  "/v1/commercial/offers",
  "/v1/commercial/capability-dependencies$",
  "/v1/commercial/quotes/preview$",
  "/v1/commercial/configurations",
  // The Verify API developer console (ZID-EXT-IDV-001) and its case review (CR-167): named one by one. The partner-facing routes of the same API (the token
  // endpoint, whoami, the hosted verification pages, the OpenAPI document) are called by a partner's server or by the person being verified with their own
  // credential, never through a signed-in person's bearer, so they stay off this list.
  "/v1/verify-api/applications",
  "/v1/verify-api/sessions",
  "/v1/verify-api/usage",
  "/v1/verify-api/cases",
  "/v1/verify-api/credential-approvals",
  "/v1/verify-api/go-live",
  "/v1/verify-api/review-cases",
  "/v1/verify-api/fraud",
  "/trust/v1/",
  "/v1/verify-api/diagnostics",
  "/v1/verify-api/errors$",
  "/v1/verify-api/sdks$",
  "/v1/verify-api/fixtures",
  "/v1/verify-api/versions",
  "/v1/verify-api/claims$",
  "/v1/verify-api/credential-policy$",
  "/v1/verify-api/scope-changes",
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
  // Dev hot-reload keeps globalThis: rebuild when the cached BFF predates the current code.
  if (g.__zidBff && typeof g.__zidBff.bff.clientFor === "function") return g.__zidBff;
  const env = readRuntimeEnv();
  const secure = env.publicOrigin.startsWith("https://") || env.publicOrigin.startsWith("http://localhost");
  const bff = createBff({
    app: "web",
    cellApiUrl: env.cellApiUrl,
    issuer: env.issuer,
    clientId: env.clientId,
    workspaceCookie: "zid_workspace",
    publicOrigin: env.publicOrigin,
    callbackPath: "/auth/callback",
    redirectUri: env.redirectUri,
    // Verification and presentation endpoints check these scopes; the first-party client holds them.
    scopes: ["openid", "verification:read", "verification:execute", "presentation:request"],
    secureCookies: secure,
    // ZID_DEVICE_COOKIE=off switches the first-party device cookie (and the x-zid-device header) off.
    deviceCookie: process.env.ZID_DEVICE_COOKIE?.trim().toLowerCase() !== "off",
    sessionTtlSeconds: 8 * 60 * 60,
    apiPathPrefixes: API_PATH_PREFIXES,
    passthroughPrefixes: PASSTHROUGH_PREFIXES,
    store: sessionStore(),
  });
  g.__zidBff = { bff, env };
  return g.__zidBff;
}
