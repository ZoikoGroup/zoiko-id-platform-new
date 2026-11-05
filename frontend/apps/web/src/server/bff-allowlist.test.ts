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
    ["provisioning sources (list, register, suspend)", "/provisioning/sources"],
  ])("lets a screen reach %s", (_what, prefix) => {
    expect(block).toContain(`"${prefix}`);
  });

  it("keeps the SCIM service and HR events off the browser proxy: external clients call the cell directly", () => {
    for (const closed of ['"/scim/', '"/provisioning/lifecycle-events']) expect(block).not.toContain(closed);
  });

  it("does not open the reviewer, operator or sign-up relays through the bearer proxy", () => {
    for (const closed of ['"/v1/reviews/', '"/v1/registration', '"/v1/containment-actions', '"/v1/recovery-cases']) expect(block).not.toContain(closed);
  });
});
