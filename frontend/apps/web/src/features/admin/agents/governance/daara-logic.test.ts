/** The wording and gating of the DAARA screens (ZID-AG-WF-001). AT-14, AT-22: an incomplete run is never a clean pass, a step never opens past a failed gate. */
import type { DaaraRun, DaaraSetup } from "@zoiko/api-client";
import { describe, expect, it } from "vitest";

import { DISMISS_REASONS, PROPOSAL_STATUS, STAGE_LABEL, STEPS, blockerText, newKey, runHeadline, runLabel, stepEnabled, timelineStatus, unlockedStep } from "./daara-logic";

const setup = (over: Partial<DaaraSetup> = {}, inner: Partial<NonNullable<DaaraSetup["setup"]>> = {}): DaaraSetup => ({
  agent_id: "a1",
  template: { template: "DAARA", purpose: "access_posture_review", rule_set_version: "1", tools: [], does: [], does_not: [], release_default: "OFF" },
  enabled: true,
  stage: "DRAFT",
  blockers: [],
  setup: { label: "x", rationale: null, warn_days: 14, region: "eu-west", ai_explain: false, schedule: "MANUAL", config_hash: "sha256:" + "a".repeat(64), validated: false, sandbox_passed: false, ...inner },
  ...over,
});

const run = (over: Partial<DaaraRun> = {}): DaaraRun => ({
  run_id: "r1", agent_id: "a1", mode: "LIVE", state: "COMPLETED", incomplete: false, checked: 42, issues: 4, proposals: 2, privileges_changed: 0,
  rule_set_version: "1", policy_hash: null, summary: {}, explanation: "", explanation_source: "FIXED", reason_code: null, completed_at: null, ...over,
});

describe("the wizard never opens a step past a gate that has not passed", () => {
  it("starts at the purpose and then follows the server's gates", () => {
    expect(unlockedStep(undefined)).toBe("purpose");
    expect(unlockedStep(setup({ enabled: false, setup: null }))).toBe("purpose");
    expect(unlockedStep(setup())).toBe("scope");
    expect(unlockedStep(setup({}, { validated: true }))).toBe("test");
    expect(unlockedStep(setup({}, { validated: true, sandbox_passed: true }))).toBe("publish");
  });
  it("keeps Publish locked until validation and the sandbox test have passed", () => {
    expect(stepEnabled(setup(), "publish")).toBe(false);
    expect(stepEnabled(setup({}, { validated: true }), "publish")).toBe(false);
    expect(stepEnabled(setup({}, { validated: true, sandbox_passed: true }), "publish")).toBe(true);
    expect(stepEnabled(undefined, "scope")).toBe(false);
  });
  it("lets a person go back and edit the earlier steps once set up", () => {
    for (const id of ["purpose", "scope", "runtime"] as const) expect(stepEnabled(setup(), id)).toBe(true);
    expect(STEPS.map((s) => s.id)).toEqual(["purpose", "scope", "runtime", "test", "publish"]);
  });
});

describe("what a stage is called", () => {
  it("says Live only for ACTIVE and describes every stage", () => {
    for (const [k, v] of Object.entries(STAGE_LABEL)) expect(v.hint.length, k).toBeGreaterThan(5);
    expect(STAGE_LABEL.ACTIVE.label).toBe("Live");
    expect(STAGE_LABEL.PENDING_APPROVAL.label).not.toMatch(/live/i);
    expect(STAGE_LABEL.SANDBOX_READY.label).not.toMatch(/live/i);
  });
});

describe("blockers are explained, with the names they carry", () => {
  it("names missing tools, and falls back safely for an unknown code", () => {
    expect(blockerText("AGENT_TOOLS_MISSING:read_mandate_metadata,create_access_review_proposal")).toMatch(/read_mandate_metadata/);
    expect(blockerText("REGION_DENIED")).toMatch(/not available here/);
    expect(blockerText("SPONSOR_INVALID")).toMatch(/sponsor/);
    expect(blockerText("SOMETHING_NEW")).toBe("A requirement is not met.");
  });
});

describe("a run is never shown as healthier than it was (AT-14)", () => {
  it("labels an incomplete run as such, not as a clean result", () => {
    expect(runLabel(run({ state: "NEEDS_REVIEW", incomplete: true })).label).toMatch(/incomplete, not a clean result/);
    expect(runLabel(run({ state: "COMPLETED" })).label).toMatch(/nothing to review/);
    expect(runLabel(run({ state: "FAILED" })).label).toMatch(/not a result/);
    expect(runLabel(run({ state: "DENIED" })).tone).toBe("danger");
  });
  it("states the counts and always the privileges changed", () => {
    expect(runHeadline(run())).toBe("Checked 42 records | 4 issues flagged | 2 review proposals | 0 privileges changed");
    expect(runHeadline(run({ checked: 1, issues: 1, proposals: 1 }))).toBe("Checked 1 record | 1 issue flagged | 1 review proposal | 0 privileges changed");
  });
  it("shows a missing step as not reached, never as done", () => {
    const t = timelineStatus(["run.start", "daara.authorized"]);
    expect(t.filter((x) => x.done).map((x) => x.step)).toEqual(["run.start", "daara.authorized"]);
    expect(t.find((x) => x.step === "daara.completed")?.done).toBe(false);
  });
});

describe("the review queue", () => {
  it("offers only controlled dismissal reasons and labels a hand-off as a hand-off", () => {
    expect(DISMISS_REASONS.map((r) => r.id)).toEqual(["NOT_A_RISK", "ALREADY_HANDLED", "DUPLICATE", "ACCEPTED_RISK"]);
    expect(PROPOSAL_STATUS.HANDED_OFF.label).toMatch(/governed access workflow/);
    expect(PROPOSAL_STATUS.HANDED_OFF.label).not.toMatch(/granted|revoked|applied/i);
  });
  it("makes a fresh idempotency key each time", () => {
    expect(newKey()).not.toBe(newKey());
    expect(newKey().length).toBeGreaterThanOrEqual(8);
  });
});
