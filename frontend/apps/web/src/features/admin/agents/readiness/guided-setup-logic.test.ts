/** CR-146: the guided setup says where a company is, who can do the next step, and never lets a builder be their own approver. */
import type { AgentDetail, AgentItem, ApiBinding, BlueprintRelease, BuildItem, ReadinessSetup, ToolItem } from "@zoiko/api-client";
import { describe, expect, it } from "vitest";

import { DAARA_BLUEPRINT, DAARA_TOOLS, GATES, type Inputs, chosenBuild, inChain, nextDaaraVersion, nextStep, planSteps, progress, stepToShow, toolProblem } from "./guided-setup-logic";

const ME = "me";
const OTHER = "other";
const DIGEST = `sha256:${"a".repeat(64)}`;

const tool = (id: string, over: Partial<ToolItem> = {}): ToolItem => {
  const spec = DAARA_TOOLS.find((t) => t.tool_id === id);
  return { tool_id: id, version: "1.0.0", server_principal_id: "srv", canonical_action: spec?.action ?? "x.y", default_risk_class: 1, side_effect: "NONE", requires_mandate: false, requires_human_approval: false, status: "ACTIVE", executable: true, ...over };
};
const bothTools = () => DAARA_TOOLS.map((t) => tool(t.tool_id));
const build = (over: Partial<BuildItem> = {}): BuildItem => ({
  build_id: "b1", name: "TESCO access readiness", artifact_digest: DIGEST, source_ref: null, provenance_ref: null, registered_by: ME, review_status: "APPROVED", approved_by: OTHER,
  registered_at: "2026-11-21T03:00:00Z", approved_at: null, revoked_at: null, revoke_reason: null, kind: "API_CLIENT", ...over,
});
const release = (over: Partial<BlueprintRelease> = {}): BlueprintRelease => ({ blueprint_id: DAARA_BLUEPRINT, version: "1.0.0", artifact_digest: DIGEST, tool_manifest_digest: DIGEST, publisher_principal_id: ME, review_status: "APPROVED", effective_at: null, ...over });
const agent = (over: Partial<AgentItem> = {}): AgentItem => ({
  agent_principal_id: "ag1", agent_name: "Access readiness", blueprint_id: DAARA_BLUEPRINT, blueprint_version: "1.0.0", publisher_principal_id: ME, operator_principal_id: ME, sponsor_principal_id: ME,
  owning_organization_id: "org", parent_agent_id: null, model_provider: "", allowed_tools: DAARA_TOOLS.map((t) => t.tool_id), max_subagent_depth: 0, status: "DRAFT", security_revision: 0, ...over,
});
const detail = (gates: string[], status: AgentItem["status"] = "DRAFT"): AgentDetail => ({
  ...agent({ status }),
  sub_agents: 0,
  readiness: {
    agent_principal_id: "ag1", status, blockers: [], attestable_gates: [...GATES], live_bindings: 1,
    attestations: Object.fromEntries(gates.map((g) => [g, { attested_by: OTHER, note: "", attested_at: "2026-11-21T03:00:00Z" }])),
  },
});
const bound: ApiBinding = { agent_id: "ag1", bound: true, binding: { binding_id: "x", status: "ACTIVE", client_id: "c", descriptor_digest: DIGEST, thumbprints: [], audiences: [], valid_to: "2027-01-01T00:00:00Z", created_at: "", revoke_reason: null } };
const setup = (status: ReadinessSetup["status"]) => ({ setup_id: "s1", agent_id: "ag1", display_name: "x", status }) as unknown as ReadinessSetup;

const inputs = (over: Partial<Inputs> = {}): Inputs => ({ me: ME, tools: [], builds: [], releases: [], agents: [], detail: null, binding: null, setups: [], serverId: "srv", ...over });
const state = (i: Inputs, id: string) => planSteps(i).find((s) => s.id === id)!;
const states = (i: Inputs) => planSteps(i).map((s) => `${s.id}:${s.state}`).join(" ");

describe("a company that has done nothing", () => {
  it("starts with the two read-only tools, and everything after waits for what comes before", () => {
    const steps = planSteps(inputs());
    expect(steps[0]).toMatchObject({ id: "tools", state: "you" });
    expect(steps.slice(1).every((s) => s.state === "blocked")).toBe(true);
    expect(stepToShow(steps)?.id).toBe("tools");
    expect(progress(steps)).toEqual({ done: 0, total: 12 });
  });
  it("says so when there is nothing to host the tools", () => {
    const s = state(inputs({ serverId: null }), "tools");
    expect(s.state).toBe("blocked");
    expect(s.detail).toMatch(/no application or service/i);
  });
  it("refuses to build on a tool of the same name that does something else", () => {
    const s = state(inputs({ tools: [tool("read_mandate_metadata", { requires_mandate: true, canonical_action: "mandate.read" })] }), "tools");
    expect(s.state).toBe("blocked");
    expect(s.detail).toMatch(/read_mandate_metadata/);
  });
  it("tells a draft tool from a missing one and an active one", () => {
    const spec = DAARA_TOOLS[0];
    expect(toolProblem([], spec)).toBe("missing");
    expect(toolProblem([tool(spec.tool_id, { status: "DRAFT" })], spec)).toBe("draft");
    expect(toolProblem([tool(spec.tool_id)], spec)).toBe("ok");
    expect(toolProblem([tool(spec.tool_id, { default_risk_class: 2 })], spec)).toBe("wrong");
    expect(toolProblem([tool(spec.tool_id, { side_effect: "REVERSIBLE" })], spec)).toBe("wrong");
  });
});

describe("a builder is never their own approver", () => {
  it("waits for someone else to approve the key I registered, and offers the button to anyone else", () => {
    const draft = [build({ review_status: "DRAFT", approved_by: null })];
    expect(state(inputs({ tools: bothTools(), builds: draft }), "client_approval").state).toBe("waiting");
    expect(state(inputs({ tools: bothTools(), builds: draft, me: OTHER }), "client_approval").state).toBe("you");
  });
  it("waits for someone else to approve the release I drafted, and offers the button to anyone else", () => {
    const i = inputs({ tools: bothTools(), builds: [build()], releases: [release({ review_status: "DRAFT" })] });
    expect(state(i, "release_approval").state).toBe("waiting");
    expect(state({ ...i, me: OTHER }, "release_approval").state).toBe("you");
  });
  it("the second person opening the same page finds their own step, because it is worked out and not remembered", () => {
    const shared = { tools: bothTools(), builds: [build({ review_status: "DRAFT", approved_by: null })] };
    expect(stepToShow(planSteps(inputs({ ...shared, me: ME })))?.id).toBe("client_approval"); // the registrant sees what they wait for
    expect(stepToShow(planSteps(inputs({ ...shared, me: OTHER })))?.id).toBe("client_approval");
    expect(state(inputs({ ...shared, me: ME }), "client_approval").state).toBe("waiting");
    expect(state(inputs({ ...shared, me: OTHER }), "client_approval").state).toBe("you");
  });
});

describe("the release is always called daara", () => {
  it("does not count a release with any other id, which is how `dara` happened", () => {
    const i = inputs({ tools: bothTools(), builds: [build()], releases: [release({ blueprint_id: "dara" }), release({ blueprint_id: "dara", version: "1.0.1" })] });
    expect(state(i, "release").state).toBe("you");
    expect(state(i, "release").detail).toContain(`${DAARA_BLUEPRINT} 1.0.0`);
  });
  it("takes the next free version of daara, and only counts daara", () => {
    expect(nextDaaraVersion([])).toBe("1.0.0");
    expect(nextDaaraVersion([release(), release({ version: "1.0.1" })])).toBe("1.0.2");
    expect(nextDaaraVersion([release({ blueprint_id: "dara" })])).toBe("1.0.0");
  });
  it("needs the key approved before it will draft the release", () => {
    expect(state(inputs({ tools: bothTools(), builds: [build({ review_status: "DRAFT" })] }), "release").state).toBe("blocked");
  });
});

describe("choosing the software", () => {
  it("ignores image builds, revoked builds and prefers an approved key, then the one its release was cut from", () => {
    const image = build({ build_id: "i", kind: "IMAGE", artifact_digest: `sha256:${"b".repeat(64)}` });
    const revoked = build({ build_id: "r", review_status: "REVOKED", registered_at: "2026-11-21T09:00:00Z" });
    const draftNewer = build({ build_id: "d", review_status: "DRAFT", registered_at: "2026-11-21T08:00:00Z", artifact_digest: `sha256:${"c".repeat(64)}` });
    const approvedOlder = build({ build_id: "a", registered_at: "2026-11-21T01:00:00Z" });
    expect(chosenBuild([image, revoked, draftNewer, approvedOlder], [])?.build_id).toBe("a");
    expect(chosenBuild([approvedOlder, draftNewer], [release({ artifact_digest: draftNewer.artifact_digest })])?.build_id).toBe("d");
    expect(chosenBuild([image, revoked], [])).toBeNull();
  });
});

describe("the four checks and switching it on", () => {
  const base = { tools: bothTools(), builds: [build()], releases: [release()], binding: bound };
  it("the person who answers for the agent can confirm three checks but not the security review", () => {
    const i = inputs({ ...base, agents: [agent()], detail: detail([]) });
    const s = state(i, "gates");
    expect(s.state).toBe("you");
    expect(s.detail).toMatch(/Switch-off tested/);
    expect(s.detail).not.toMatch(/Confirm: Security review/);
    expect(s.detail).toMatch(/security review is for someone else/i);
  });
  it("waits for someone outside the chain once only the security review is left", () => {
    const i = inputs({ ...base, agents: [agent()], detail: detail(["KILL_PATH_TESTED", "SHORT_LIVED_CREDENTIALS", "OBSERVABILITY"]) });
    expect(state(i, "gates").state).toBe("waiting");
    expect(state({ ...i, me: OTHER }, "gates").state).toBe("you");
  });
  it("sends the agent for activation, then needs someone outside the chain to switch it on", () => {
    const all = [...GATES];
    expect(state(inputs({ ...base, agents: [agent({ status: "DRAFT" })], detail: detail(all) }), "activation").state).toBe("you");
    const pending = inputs({ ...base, agents: [agent({ status: "PENDING_APPROVAL" })], detail: detail(all, "PENDING_APPROVAL") });
    expect(state(pending, "activation").state).toBe("waiting");
    expect(state({ ...pending, me: OTHER }, "activation").state).toBe("you");
    expect(state(inputs({ ...base, agents: [agent({ status: "ACTIVE" })], detail: detail(all, "ACTIVE") }), "activation").state).toBe("done");
  });
  it("does not offer activation before the four checks", () => {
    expect(state(inputs({ ...base, agents: [agent()], detail: detail(["KILL_PATH_TESTED"]) }), "activation").state).toBe("blocked");
  });
  it("sponsor, operator and publisher are all the chain", () => {
    expect(inChain(agent({ sponsor_principal_id: OTHER, operator_principal_id: OTHER, publisher_principal_id: ME }), ME)).toBe(true);
    expect(inChain(agent({ sponsor_principal_id: OTHER, operator_principal_id: OTHER, publisher_principal_id: OTHER }), ME)).toBe(false);
    expect(inChain(undefined, ME)).toBe(false);
  });
});

describe("the whole path, one step at a time", () => {
  it("reaches the run, and reports each step as it goes", () => {
    const all = [...GATES];
    const live = { tools: bothTools(), builds: [build()], releases: [release()], binding: bound, agents: [agent({ status: "ACTIVE" })], detail: detail(all, "ACTIVE") };
    expect(states(inputs(live))).toBe(
      "tools:done client:done client_approval:done release:done release_approval:done agent:done binding:done gates:done activation:done setup:you publish:blocked run:blocked",
    );
    const drafting = planSteps(inputs({ ...live, setups: [setup("SANDBOX_READY")] }));
    expect(drafting.find((s) => s.id === "publish")?.state).toBe("you");
    const approved = planSteps(inputs({ ...live, setups: [setup("APPROVED")] }));
    expect(approved.find((s) => s.id === "run")?.state).toBe("you");
    expect(progress(approved)).toEqual({ done: 11, total: 12 });
    expect(nextStep(approved)?.id).toBe("run");
  });
  it("every step says why it exists and what right it needs", () => {
    for (const s of planSteps(inputs())) {
      expect(s.why.length).toBeGreaterThan(20);
      expect(s.right).toMatch(/^[a-z_.]+$/);
    }
  });
  it("a revoked or retired agent is not the agent, so the step to create one comes back", () => {
    const live = { tools: bothTools(), builds: [build()], releases: [release()] };
    expect(state(inputs({ ...live, agents: [agent({ status: "REVOKED" })] }), "agent").state).toBe("you");
    expect(state(inputs({ ...live, agents: [agent({ status: "RETIRED" })] }), "agent").state).toBe("you");
  });
});
