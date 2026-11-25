import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

/**
 * The browser reaches the cell only through the proxy's allowlist. A console screen whose route is missing from it fails on a real stack
 * ("This page isn't available") while every mocked test passes, so the routes the Phase L screens call are pinned here.
 */
const source = readFileSync(join(__dirname, "bff.ts"), "utf8");
const block = source.slice(source.indexOf("const API_PATH_PREFIXES"), source.indexOf("const PASSTHROUGH_PREFIXES"));

describe("the proxy allowlist", () => {
  it.each([
    ["organization setup, approvals and decisions", "/v1/organizations/"],
    ["approval cases (reviewers, recovery, evidence)", "/v1/approval-cases"],
    ["approval policy", "/v1/approval-policy"],
    ["governance (reviewers, solo status)", "/v1/governance/"],
    ["step-up challenges and proofs", "/v1/step-up/"],
    ["company claims", "/v1/org-claims"],
    ["plan features and downgrade impact", "/v1/entitlements"],
    ["suspend, resume and offboard on the settings page", "/v1/tenant:suspend"],
    ["resume after a suspension", "/v1/tenant:resume"],
    ["offboard an organization", "/v1/tenant:offboard"],
    ["the billing overview, profile, commitments and delegates", "/v1/billing/"],
    ["billing grants and the caller's own permissions", "/v1/tenant/"],
    ["the role gallery (the published role matrix)", "/v1/roles/"],
    ["the verification level and what each tier asks for", "/v1/verification/"],
    ["the solution builder offers by market","/v1/commercial/offers"],
    ["service dependencies for the solution builder","/v1/commercial/capability-dependencies"],
    ["the quote preview","/v1/commercial/quotes/preview"],
    ["saved solution configurations and quote requests","/v1/commercial/configurations"],
    ["the access readiness agent setup, runs and review proposals", "/v1/access-readiness/"],
    ["provisioning sources (list, register, suspend)", "/provisioning/sources"],
    ["directory syncs (connect, run, pause, resume, replace the secret)", "/provisioning/directory-syncs"],
    ["the trust store for chip signing certificates", "/v2/trust-store/"],
    ["the verification supplier budget", "/verification-budget"],
    ["the verification usage counts", "/verification-usage"],
    ["the face engine readiness view", "/verification-engine-readiness"],
    ["the identity method certification board", "/verification-method-certification"],
    ["the age check", "/v2/age-checks"],
    ["the company verification grade and mandates", "/v1/kyb/"],
    ["screening eligibility", "/v1/screening/eligibility"],
    ["screening cases and their minimal status", "/v1/screening/cases"],
    ["screening owners roll-up", "/v1/screening/ownership/rollup"],
    ["identity proofing cases", "/v1/idv/"],
    ["Verify API applications, credentials and their sessions", "/v1/verify-api/applications"],
    ["Verify API results", "/v1/verify-api/sessions"],
    ["Verify API usage", "/v1/verify-api/usage"],
    ["Verify API disputes, appeals and erasure requests", "/v1/verify-api/cases"],
    ["Verify API credential approvals", "/v1/verify-api/credential-approvals"],
    ["Verify API go-live requests", "/v1/verify-api/go-live"],
    ["Verify API case review and four-eyes (CR-167)", "/v1/verify-api/review-cases"],
    ["Verify API endpoint health (CR-173)", "/v1/verify-api/diagnostics"],
    ["the error catalog", "/v1/verify-api/errors$"],
    ["the SDK register", "/v1/verify-api/sdks$"],
    ["sandbox personas and plans", "/v1/verify-api/fixtures"],
    ["API versions and their schemas", "/v1/verify-api/versions"],
    ["the public claims feed", "/v1/verify-api/claims$"],
    ["the credential policy", "/v1/verify-api/credential-policy$"],
    ["Verify API scope changes and approvals (CR-173)", "/v1/verify-api/scope-changes"],
    ["the issuer trust bundle (CR-170)", "/v1/trust/issuer$"],
    ["provider coverage and routing (CR-171)", "/v1/routing/"],
  ])("lets a screen reach %s", (_what, prefix) => {
    expect(block).toContain(`"${prefix}`);
  });

  it("keeps the SCIM service and HR events off the browser proxy: external clients call the cell directly", () => {
    for (const closed of ['"/scim/', '"/provisioning/lifecycle-events']) expect(block).not.toContain(closed);
  });

  it("keeps the partner-facing Verify API routes off the browser proxy: a partner's server and the person being verified use their own credential", () => {
    for (const closed of ['"/v1/verify-api/oauth2', '"/v1/verify-api/whoami', '"/v1/verify-api/hosted', '"/v1/verify-api/openapi', '"/v1/verify-api/workflows', '"/v1/verify-api/products', '"/v1/verify-api/"']) expect(block).not.toContain(closed);
  });

  it("does not open the reviewer, operator or sign-up relays through the bearer proxy", () => {
    for (const closed of ['"/v1/reviews/', '"/v1/registration', '"/v1/containment-actions', '"/v1/recovery-cases', '"/v1/commercial/market-matrices', '"/v1/commercial/quote-requests']) expect(block).not.toContain(closed);
  });
});
