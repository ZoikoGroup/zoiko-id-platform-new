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
  ])("lets a screen reach %s", (_what, prefix) => {
    expect(block).toContain(`"${prefix}`);
  });

  it("does not open the reviewer, operator or sign-up relays through the bearer proxy", () => {
    for (const closed of ['"/v1/reviews/', '"/v1/registration', '"/v1/containment-actions', '"/v1/recovery-cases']) expect(block).not.toContain(closed);
  });
});
