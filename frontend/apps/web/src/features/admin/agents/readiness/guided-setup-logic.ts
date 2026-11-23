/**
 * The guided setup of the access readiness agent (CR-146): where a company is, and what comes next, worked out from what the server holds.
 *
 * Setting up the agent takes a dozen steps across the registry (tools, a key, a release, the agent, its binding, its checks, its approval) and two people, because the
 * platform will not let the person who builds an agent approve it. A person should not need to know any of that. This reads the company's tools, builds, releases,
 * agents, binding and setups and says, for each step, whether it is done, whether it is theirs to do, whether it is waiting for someone else, or whether something earlier
 * must come first. Nothing is stored: the second person opens the same page and finds their own step, because it is derived, not remembered.
 *
 * Pure: no network, no clock. The page runs the actions; the server still decides every one of them.
 */

import type { AgentDetail, AgentGate, AgentItem, ApiBinding, BlueprintRelease, BuildItem, ReadinessSetup, ToolItem } from "@zoiko/api-client";

/** The blueprint id the access readiness agent requires, exactly. Typed by hand it is easy to get wrong (`dara`), so the page never asks for it. */
export const DAARA_BLUEPRINT = "daara";
export const DAARA_VERSION = "1.0.0";

/** The two read tools the agent holds. An agent bound to an API client may hold only tools that read or can be undone, so the write of a review proposal is the platform's own. */
export const DAARA_TOOLS = [
  { tool_id: "read_mandate_metadata", action: "mandate.metadata.read", plain: "Read a mandate's dates, scope and sponsor (no names, no amounts, no documents)" },
  { tool_id: "read_agent_identity_status", action: "workload_identity.status.read", plain: "Read whether an agent is active and its sponsor still active (no credentials)" },
] as const;

export const GATES: AgentGate[] = ["SECURITY_REVIEW", "KILL_PATH_TESTED", "SHORT_LIVED_CREDENTIALS", "OBSERVABILITY"];

export const GATE_PLAIN: Record<AgentGate, { title: string; note: string }> = {
  SECURITY_REVIEW: { title: "Security review", note: "Threat, model and tool review completed; reviewed by someone independent of the sponsor, operator and publisher." },
  KILL_PATH_TESTED: { title: "Switch-off tested", note: "Suspended and revoked in a drill; the agent stopped." },
  SHORT_LIVED_CREDENTIALS: { title: "Short-lived credentials", note: "The agent holds no long-lived secret: a key proves it, and its token lasts minutes." },
  OBSERVABILITY: { title: "Monitoring", note: "Every run leaves evidence and the monitoring page shows it." },
};

export type StepId = "tools" | "client" | "client_approval" | "release" | "release_approval" | "agent" | "binding" | "gates" | "activation" | "setup" | "publish" | "run";
/** done: finished. you: this person can do it now. waiting: only someone else can (the platform keeps builders and approvers apart). blocked: an earlier step comes first, or something is wrong. */
export type StepState = "done" | "you" | "waiting" | "blocked";

export type Step = {
  id: StepId;
  title: string;
  /** Why this step exists, in plain words. */
  why: string;
  state: StepState;
  /** What to say about a step that is not done: what to press, who has to act, or what is in the way. */
  detail: string;
  /** What a finished step says it did. */
  doneText?: string;
  /** The action the signed-in person needs for this step. */
  right?: string;
};

export type Inputs = {
  me: string;
  tools: ToolItem[];
  builds: BuildItem[];
  releases: BlueprintRelease[];
  agents: AgentItem[];
  /** The readiness of the access readiness agent, once it exists. */
  detail?: AgentDetail | null;
  binding?: ApiBinding | null;
  setups: ReadinessSetup[];
  /** An application or service of the company that can host the tools; none means the tools cannot be registered yet. */
  serverId: string | null;
};

const LIVE = (a: AgentItem) => a.status !== "REVOKED" && a.status !== "RETIRED";

export function toolProblem(tools: ToolItem[], spec: (typeof DAARA_TOOLS)[number]): "missing" | "draft" | "wrong" | "ok" {
  const t = tools.find((x) => x.tool_id === spec.tool_id);
  if (!t) return "missing";
  if (t.canonical_action !== spec.action || t.default_risk_class > 1 || t.side_effect !== "NONE" || t.requires_mandate || t.status === "RETIRED") return "wrong";
  return t.status === "ACTIVE" ? "ok" : "draft";
}

/** The software the agent runs: the build its release is cut from, else the newest build that is not revoked (an approved one first). API clients only. */
export function chosenBuild(builds: BuildItem[], releases: BlueprintRelease[]): BuildItem | null {
  const clients = builds.filter((b) => b.kind === "API_CLIENT" && b.review_status !== "REVOKED");
  const release = releases.find((r) => r.blueprint_id === DAARA_BLUEPRINT && r.review_status !== "REVOKED");
  const named = release ? clients.find((b) => b.artifact_digest === release.artifact_digest) : undefined;
  if (named) return named;
  const byNewest = [...clients].sort((a, b) => b.registered_at.localeCompare(a.registered_at));
  return byNewest.find((b) => b.review_status === "APPROVED") ?? byNewest[0] ?? null;
}

/** The first version number free for the `daara` release: 1.0.0, then 1.0.1 and so on (a release never changes, so a fix is a new version). */
export function nextDaaraVersion(releases: BlueprintRelease[]): string {
  const taken = new Set(releases.filter((r) => r.blueprint_id === DAARA_BLUEPRINT).map((r) => r.version));
  let patch = 0;
  while (taken.has(`1.0.${patch}`)) patch += 1;
  return `1.0.${patch}`;
}

export function inChain(agent: AgentItem | undefined, me: string): boolean {
  return Boolean(agent && [agent.sponsor_principal_id, agent.operator_principal_id, agent.publisher_principal_id].includes(me));
}

export function planSteps(i: Inputs): Step[] {
  const build = chosenBuild(i.builds, i.releases);
  const release = i.releases.find((r) => r.blueprint_id === DAARA_BLUEPRINT && r.review_status !== "REVOKED" && (!build || r.artifact_digest === build.artifact_digest));
  const agent = i.agents.find((a) => a.blueprint_id === DAARA_BLUEPRINT && LIVE(a));
  const setup = agent ? i.setups.find((s) => s.agent_id === agent.agent_principal_id) : undefined;
  const attested = new Set(Object.keys(i.detail?.readiness?.attestations ?? {}) as AgentGate[]);
  const gatesDone = GATES.every((g) => attested.has(g));
  const mine = inChain(agent, i.me);
  const bound = Boolean(i.binding?.bound && i.binding.binding?.status === "ACTIVE");
  const steps: Step[] = [];
  const add = (s: Step) => steps.push(s);
  const blockedBy = (what: string): Pick<Step, "state" | "detail"> => ({ state: "blocked", detail: `This comes after: ${what}.` });

  // 1 the two read tools
  const problems = DAARA_TOOLS.map((s) => ({ s, p: toolProblem(i.tools, s) }));
  const wrong = problems.find((x) => x.p === "wrong");
  const toolsDone = problems.every((x) => x.p === "ok");
  add({
    id: "tools",
    title: "The two things the agent may read",
    why: "The agent is given exactly two read-only abilities, and nothing else. It can see a mandate's dates and scope, and whether another agent is still active. It cannot change anything.",
    right: "machine.tool.manage",
    doneText: "Both read-only tools are registered and active.",
    ...(toolsDone
      ? { state: "done" as const, detail: "" }
      : wrong
        ? { state: "blocked" as const, detail: `A tool named ${wrong.s.tool_id} already exists with different settings (it may need a mandate, or do something else). Retire it, then this step can create the right one.` }
        : !i.serverId
          ? { state: "blocked" as const, detail: "The company has no application or service to host the tools yet. Ask the platform operator to add one." }
          : { state: "you" as const, detail: "Press the button and both tools are created and switched on." }),
  });

  // 2 the key
  add({
    id: "client",
    title: "How the agent proves who it is",
    why: "The agent signs in with a key it keeps; the platform only ever holds the public half. You make the key here, and the private half is saved to your computer once.",
    right: "machine.build.register",
    doneText: build ? `Registered: ${build.name}.` : undefined,
    ...(build ? { state: "done" as const, detail: "" } : toolsDone ? { state: "you" as const, detail: "Name it, make a key and register it." } : blockedBy("the two read-only tools")),
  });

  // 3 the key approved by someone else
  add({
    id: "client_approval",
    title: "A second person approves the key",
    why: "The person who registers an agent's key may not approve it. A second person checks it first.",
    right: "machine.build.manage",
    doneText: "The key is approved.",
    ...(!build
      ? blockedBy("registering the key")
      : build.review_status === "APPROVED"
        ? { state: "done" as const, detail: "" }
        : build.registered_by === i.me
          ? { state: "waiting" as const, detail: "You registered this key, so someone else must approve it. Ask a colleague with the right to approve software builds to open this page and press the button." }
          : { state: "you" as const, detail: `Check that the key ("${build.name}") is the one you expect, then approve it.` }),
  });

  // 4 the release
  add({
    id: "release",
    title: "Describe the agent",
    why: `Every agent is cut from a release that names its software and its tools. The access readiness agent's release is always called "${DAARA_BLUEPRINT}": you do not type it.`,
    right: "machine.blueprint.manage",
    doneText: release ? `Release ${release.blueprint_id} ${release.version}.` : undefined,
    ...(release
      ? { state: "done" as const, detail: "" }
      : build?.review_status === "APPROVED"
        ? { state: "you" as const, detail: `Creates release ${DAARA_BLUEPRINT} ${nextDaaraVersion(i.releases)} from the approved key and the two tools.` }
        : blockedBy("approving the key")),
  });

  // 5 the release approved by someone else
  add({
    id: "release_approval",
    title: "A second person approves the description",
    why: "The person who drafts a release may not approve it either.",
    right: "machine.blueprint.manage",
    doneText: "The release is approved.",
    ...(!release
      ? blockedBy("describing the agent")
      : release.review_status === "APPROVED"
        ? { state: "done" as const, detail: "" }
        : release.publisher_principal_id === i.me
          ? { state: "waiting" as const, detail: "You drafted this release, so someone else must approve it. Ask a colleague to open this page and press the button." }
          : { state: "you" as const, detail: `Check release ${release.blueprint_id} ${release.version}, then approve it.` }),
  });

  // 6 the agent
  add({
    id: "agent",
    title: "Create the agent",
    why: "This gives the agent its own identity, with a named person who is accountable for it (its sponsor).",
    right: "machine.agent.manage",
    doneText: agent ? `Created: ${agent.agent_name}.` : undefined,
    ...(agent ? { state: "done" as const, detail: "" } : release?.review_status === "APPROVED" ? { state: "you" as const, detail: "Name it and choose who answers for it." } : blockedBy("approving the description")),
  });

  // 7 binding
  add({
    id: "binding",
    title: "Connect the key to the agent",
    why: "This is what lets the agent sign in with its key, and only while the key stays approved.",
    right: "machine.agent.manage",
    doneText: "The key is connected.",
    ...(bound ? { state: "done" as const, detail: "" } : agent ? { state: "you" as const, detail: "One press." } : blockedBy("creating the agent")),
  });

  // 8 the four checks
  const mayReview = !mine;
  const missing = GATES.filter((g) => !attested.has(g));
  const mineToDo = missing.filter((g) => g !== "SECURITY_REVIEW" || mayReview);
  add({
    id: "gates",
    title: "Confirm the four safety checks",
    why: "Before an agent can be switched on, a person confirms the security review, the switch-off test, short-lived credentials and monitoring. The security review must be by someone who is not the agent's sponsor, operator or publisher.",
    right: "machine.agent.manage",
    doneText: "All four checks are confirmed.",
    ...(gatesDone
      ? { state: "done" as const, detail: "" }
      : !agent || !bound
        ? blockedBy("connecting the key")
        : mineToDo.length
          ? {
              state: "you" as const,
              detail: `Confirm: ${mineToDo.map((g) => GATE_PLAIN[g].title).join(", ")}.${missing.includes("SECURITY_REVIEW") && !mayReview ? " The security review is for someone else." : ""}`,
            }
          : { state: "waiting" as const, detail: "The security review must be done by someone who is not the sponsor, operator or publisher of this agent. Ask a colleague to open this page and press the button." }),
  });

  // 9 switch it on
  add({
    id: "activation",
    title: "Switch the agent on",
    why: "The last person to look is not one of the people who built it. Until then the agent cannot do anything.",
    right: "machine.agent.activate",
    doneText: "The agent is active.",
    ...(agent?.status === "ACTIVE"
      ? { state: "done" as const, detail: "" }
      : !gatesDone
        ? blockedBy("the four safety checks")
        : agent?.status === "DRAFT"
          ? { state: "you" as const, detail: "Send it for activation. Someone else then switches it on." }
          : mine
            ? { state: "waiting" as const, detail: "You answer for this agent, so someone else must switch it on. Ask a colleague with the right to activate agents to open this page." }
            : { state: "you" as const, detail: "Check the agent, then switch it on." }),
  });

  // 10 what it checks
  add({
    id: "setup",
    title: "Choose what it checks",
    why: "Name it, say how many days ahead to warn about expiring mandates, and run a practice run on made-up data that changes nothing.",
    right: "access_readiness.manage",
    doneText: "The setup exists.",
    ...(setup ? { state: "done" as const, detail: "" } : agent?.status === "ACTIVE" ? { state: "you" as const, detail: "Open the setup." } : blockedBy("switching the agent on")),
  });

  // 11 approve the setup
  add({
    id: "publish",
    title: "A second person approves the setup",
    why: "The agent only runs against a setup that someone other than the person who requested it has approved.",
    right: "access_readiness.approve",
    doneText: "The setup is approved.",
    ...(!setup
      ? blockedBy("choosing what it checks")
      : setup.status === "APPROVED"
        ? { state: "done" as const, detail: "" }
        : setup.status === "PENDING_APPROVAL"
          ? { state: "you" as const, detail: "Waiting for approval. The person who requested it cannot approve it: someone else opens the setup and approves." }
          : { state: "you" as const, detail: "Finish the setup (check it, practise, request approval)." }),
  });

  // 12 run
  add({
    id: "run",
    title: "Run it, then read what it found",
    why: "A run reads your company's mandates and suggests reviews for a person. It never changes any access.",
    right: "access_readiness.manage",
    ...(setup?.status === "APPROVED" ? { state: "you" as const, detail: "Start a run." } : blockedBy("approving the setup")),
  });

  return steps;
}

export function nextStep(steps: Step[]): Step | undefined {
  return steps.find((s) => s.state !== "done");
}

/** The step to show first: the first one this person can do now, else the first not done (so a waiting person sees what it waits for). */
export function stepToShow(steps: Step[]): Step | undefined {
  return steps.find((s) => s.state === "you") ?? nextStep(steps);
}

export function progress(steps: Step[]): { done: number; total: number } {
  return { done: steps.filter((s) => s.state === "done").length, total: steps.length };
}
