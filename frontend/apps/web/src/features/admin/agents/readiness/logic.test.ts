import { ApiError, type ReadinessRun, type ReadinessSetup } from "@zoiko/api-client";
import { describe, expect, it } from "vitest";

import { canRequestPublish, classify, fieldIssues, firstInvalid, publishGates, timelineSteps } from "./logic";

const err = (status: number, code: string, details: { field?: string; issue: string }[] = []) => new ApiError({ status, code, message: code, details });

const setup = (over: Partial<ReadinessSetup> = {}): ReadinessSetup => ({
  setup_id: "s1",
  agent_id: "a1",
  display_name: "Access Readiness",
  purpose: "access_posture_review",
  scope: { resource_kinds: ["mandates"], max_records: 100, warning_window_days: 14, home_region: "eu" },
  rule_set_version: "daara-rules/1",
  policy_version: "daara-policy/1",
  status: "SANDBOX_READY",
  draft_revision: 2,
  scope_digest: `sha256:${"a".repeat(64)}`,
  validated: true,
  paused_reason: null,
  approval: null,
  permitted_tools: [],
  blocked: [],
  agent: { status: "ACTIVE", sponsor_active: true, attested: true, blueprint: "b@1" },
  ...over,
});

const run = (over: Partial<ReadinessRun> = {}): ReadinessRun => ({
  run_id: "r1",
  agent_id: "a1",
  state: "COMPLETED",
  attempt: 1,
  mode: "manual",
  purpose: "access_posture_review",
  resource_selector: {},
  denial_codes: [],
  summary: { records_read: 4, proposals_created: 1 },
  rule_set_version: "daara-rules/1",
  policy_hash: "sha256:x",
  run_definition_hash: "sha256:y",
  region: "eu",
  trace_id: null,
  worker_attestation_id: "w1",
  created_at: "2026-11-14T10:00:00Z",
  terminal_at: "2026-11-14T10:00:05Z",
  links: { self: "" },
  ...over,
});

describe("classify", () => {
  it("tells the feature switch (generic 404) from a missing object", () => {
    expect(classify(err(404, "NOT_FOUND"))).toBe("off");
    expect(classify(err(404, "RUN_NOT_FOUND"))).toBe("missing");
  });
  it("tells an unsupported region and a forbidden role", () => {
    expect(classify(err(422, "REGION_UNSUPPORTED"))).toBe("region");
    expect(classify(err(403, "PROTECTED_ACTION_DENIED"))).toBe("forbidden");
    expect(classify(err(500, "X"))).toBe("other");
  });
});

describe("release gates", () => {
  it("allows a publishing request only when every gate holds", () => {
    expect(canRequestPublish(setup())).toBe(true);
    expect(canRequestPublish(setup({ validated: false }))).toBe(false);
    expect(canRequestPublish(setup({ agent: { status: "ACTIVE", sponsor_active: false, attested: true, blueprint: "b@1" } }))).toBe(false);
    expect(canRequestPublish(setup({ status: "PENDING_APPROVAL" }))).toBe(false);
    expect(canRequestPublish(null)).toBe(false);
  });
  it("says why a gate is not met", () => {
    expect(publishGates(setup({ validated: false })).find((g) => g.id === "validated")?.why).toMatch(/Run it again/);
  });
});

describe("server field errors", () => {
  it("maps a refusal code to the field and focuses the first in form order", () => {
    const issues = fieldIssues(err(422, "NAME_INVALID"));
    expect(issues.display_name).toMatch(/1 to 80/);
    expect(firstInvalid({ max_records: "x", display_name: "y" })).toBe("display_name");
  });
  it("uses the field the server names", () => {
    expect(fieldIssues(err(422, "VALIDATION_FAILED", [{ field: "body.max_records", issue: "Must be at most 500" }])).max_records).toBe("Must be at most 500");
  });
});

describe("timeline", () => {
  it("is never green for a broken chain", () => {
    const steps = timelineSteps(run(), [], { verified: false, integrity_exceptions: ["CHAIN_BROKEN:2"], items: [] });
    expect(steps.some((s) => s.state === "unverified")).toBe(true);
    expect(steps.some((s) => s.state === "done")).toBe(false);
  });
  it("stops at authorization for a denied run", () => {
    const steps = timelineSteps(run({ state: "DENIED", worker_attestation_id: null, summary: null }), undefined, undefined);
    expect(steps.find((s) => s.id === "authorized")?.state).toBe("stopped");
    expect(steps.find((s) => s.id === "completed")?.state).toBe("pending");
  });
});
