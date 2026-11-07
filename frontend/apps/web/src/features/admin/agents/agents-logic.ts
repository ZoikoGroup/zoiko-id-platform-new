import type { AgentGate, AgentItem, BlueprintRelease, BuildItem, ToolItem, ToolSideEffect, WorkloadBindingView } from "@zoiko/api-client";

import { ACTION_GROUPS } from "../authority/choice-list";

/* The decisions behind the Agents screens, kept out of the components so they can be tested: what a tool usually is, what the
 * next version is, what the setup still needs, and what the signed-in person's grants do not yet allow. */

export type Opt = { id: string; label: string; description?: string };

/* ----------------------------------------------------------------------------------------------- tools */

export type ToolPreset = {
  id: string;
  label: string;
  description: string;
  toolId: string;
  action: string;
  risk: 0 | 1 | 2 | 3;
  effect: ToolSideEffect;
  mandate: boolean;
  human: boolean;
};

/** The tools a company most often wants an agent to have. Picking one fills the form; every field stays editable. */
export const TOOL_PRESETS: ToolPreset[] = [
  {
    id: "invoice-approve",
    label: "Approve invoices",
    description: "Decides an invoice. Can be undone, so no mandate is forced.",
    toolId: "invoices.approve",
    action: "invoice.approve",
    risk: 2,
    effect: "REVERSIBLE",
    mandate: false,
    human: false,
  },
  {
    id: "payment-release",
    label: "Release payments",
    description: "Moves company money out. Always needs a mandate and, here, a person's approval.",
    toolId: "payments.release",
    action: "payment.release",
    risk: 3,
    effect: "EXTERNAL_TRANSACTION",
    mandate: true,
    human: true,
  },
  {
    id: "purchase-create",
    label: "Create purchases",
    description: "Commits the company to a purchase. Always needs a mandate.",
    toolId: "purchases.create",
    action: "purchase.create",
    risk: 3,
    effect: "EXTERNAL_TRANSACTION",
    mandate: true,
    human: false,
  },
  {
    id: "evidence-read",
    label: "Read evidence and audit",
    description: "Looks things up and changes nothing.",
    toolId: "evidence.lookup",
    action: "evidence.read",
    risk: 1,
    effect: "NONE",
    mandate: false,
    human: false,
  },
];

export const CUSTOM_PRESET = "custom";

/** Every action the dialog knows, labelled with its feature area, for a dropdown. */
export const ACTION_OPTIONS: Opt[] = ACTION_GROUPS.flatMap((g) =>
  g.choices.map((c) => ({ id: c.id, label: c.label, description: `${g.label} · ${c.id}` })),
);

/** Risk and side-effect rules the cell enforces: these two side-effect classes always need a mandate. */
export const forcesMandate = (effect: ToolSideEffect) => effect === "IRREVERSIBLE" || effect === "EXTERNAL_TRANSACTION";

/* ------------------------------------------------------------------------------------------- blueprints */

const SEMVER = /^(\d+)\.(\d+)\.(\d+)$/;

/** The version to offer for a new release of an existing blueprint: the highest x.y.z so far with its patch number raised. */
export function nextVersion(existing: string[]): string {
  let best: [number, number, number] | null = null;
  for (const v of existing) {
    const m = SEMVER.exec(v.trim());
    if (!m) continue;
    const t: [number, number, number] = [Number(m[1]), Number(m[2]), Number(m[3])];
    if (!best || t[0] > best[0] || (t[0] === best[0] && (t[1] > best[1] || (t[1] === best[1] && t[2] > best[2])))) best = t;
  }
  return best ? `${best[0]}.${best[1]}.${best[2] + 1}` : "1.0.0";
}

/**
 * The tool manifest digest of a release, computed from the tools chosen for it instead of pasted: SHA-256 over the sorted
 * `tool_id@version` lines. The cell checks only its shape, so this is a commitment the release makes about its tool list, and
 * choosing the same tools always gives the same digest.
 */
export async function manifestDigest(tools: { tool_id: string; version: string }[]): Promise<string> {
  const lines = tools.map((t) => `${t.tool_id}@${t.version}`).sort();
  const bytes = new TextEncoder().encode(lines.join("\n"));
  const hash = await globalThis.crypto.subtle.digest("SHA-256", bytes);
  return `sha256:${[...new Uint8Array(hash)].map((b) => b.toString(16).padStart(2, "0")).join("")}`;
}

/* ----------------------------------------------------------------------------------------- build choice */

export const BUILD_TEST = "test";
export const BUILD_PASTE = "paste";
const REUSE = "reuse:";

/**
 * How a release says which software it runs, without anyone needing to know a digest: the approved builds first, by name. Where
 * approved builds are not required (a local, test or development setup) earlier releases' builds, a test build and pasting a
 * digest are also offered; where they are required only an approved build can be chosen. A real digest cannot be invented from a
 * name: only the build itself has one, so it is entered once, when the build is registered.
 */
export function buildChoices(x: {
  builds: { name: string; artifact_digest: string; review_status: string }[];
  releases: { blueprint_id: string; version: string; artifact_digest: string }[];
  enforced: boolean;
  allowTest: boolean;
}): Opt[] {
  const seen = new Set<string>();
  const out: Opt[] = [];
  for (const b of x.builds) {
    if (b.review_status !== "APPROVED" || seen.has(b.artifact_digest)) continue;
    seen.add(b.artifact_digest);
    out.push({ id: `${REUSE}${b.artifact_digest}`, label: b.name, description: `${b.artifact_digest.slice(0, 19)}… · approved` });
  }
  if (x.enforced) return out;
  for (const r of [...x.releases].reverse()) {
    if (seen.has(r.artifact_digest)) continue;
    seen.add(r.artifact_digest);
    out.push({ id: `${REUSE}${r.artifact_digest}`, label: `Same build as ${r.blueprint_id} ${r.version}`, description: `${r.artifact_digest.slice(0, 19)}…` });
  }
  if (x.allowTest) {
    out.push({ id: BUILD_TEST, label: "A test build (for trying things out)", description: "A placeholder, not real software. Fine for the screens; not for running an agent." });
  }
  out.push({ id: BUILD_PASTE, label: "A build from my registry…", description: "Paste its sha256 digest." });
  return out;
}

/** The digest a choice stands for, or null while it is still to be worked out or typed. */
export function digestFor(choice: string, pasted: string, testDigest: string | undefined): string | null {
  if (choice.startsWith(REUSE)) return choice.slice(REUSE.length);
  if (choice === BUILD_TEST) return testDigest ?? null;
  if (choice === BUILD_PASTE) return pasted.trim() || null;
  return null;
}

/** A stable placeholder digest for a test release: the same blueprint and version always give the same one. Never a real build. */
export async function testBuildDigest(blueprintId: string, version: string): Promise<string> {
  const bytes = new TextEncoder().encode(`zoiko-test-build:${blueprintId}@${version}`);
  const hash = await globalThis.crypto.subtle.digest("SHA-256", bytes);
  return `sha256:${[...new Uint8Array(hash)].map((b) => b.toString(16).padStart(2, "0")).join("")}`;
}

/** Test builds are for development and demos: hidden in a production build unless that deployment turns them on. */
export const allowTestBuilds = () => process.env.NODE_ENV !== "production" || process.env.NEXT_PUBLIC_ZID_ALLOW_TEST_BUILDS === "true";

/* ------------------------------------------------------------------------------------------------ authority */

/** Does one held action cover a needed one? `*` covers all, `machine.*` covers everything under machine. */
const covers = (held: string, needed: string) => held === "*" || held === needed || (held.endsWith(".*") && needed.startsWith(held.slice(0, -1)));

/**
 * The actions among `needed` that the person's grants do not hold. `null` means a grant with no action limit (anything goes).
 * This only explains an error the cell would give: the cell still decides every request.
 */
export function missingActions(held: string[] | null, needed: string[]): string[] {
  if (held === null) return [];
  return needed.filter((n) => !held.some((h) => covers(h, n)));
}

/** The union of the actions the person's active grants carry; `null` when any of them has no action limit. */
export function heldActions(grants: { actions: string[] | null }[]): string[] | null {
  const all = new Set<string>();
  for (const g of grants) {
    if (g.actions === null) return null;
    for (const a of g.actions) all.add(a);
  }
  return [...all];
}

/** What each thing the Agents screens create needs the person's grants to hold. */
export const NEEDS = {
  tool: ["machine.tool.manage"],
  blueprint: ["machine.blueprint.manage"],
  build: ["machine.build.register"],
  approveBuild: ["machine.build.manage"],
  agent: ["machine.agent.manage"],
  activate: ["machine.agent.activate"],
  workload: ["machine.workload.manage"],
} as const;

/* ------------------------------------------------------------------------------------------ setup guide */

export type SetupStep = { id: string; title: string; detail: string; done: boolean; tab: "tools" | "builds" | "blueprints" | "agents" | "workloads" };

/** The order to set an agent up in, and where each step stands. The first step not done is the one to do next. */
export function setupSteps(x: {
  tools: ToolItem[];
  blueprints: BlueprintRelease[];
  agents: AgentItem[];
  bindings: WorkloadBindingView[];
  builds?: BuildItem[];
  buildsEnforced?: boolean;
}): SetupStep[] {
  const liveTools = x.tools.filter((t) => t.status === "ACTIVE");
  const approved = x.blueprints.filter((b) => b.review_status === "APPROVED");
  const live = x.agents.filter((a) => a.status === "ACTIVE");
  const open = x.agents.filter((a) => a.status !== "REVOKED" && a.status !== "RETIRED");
  const bound = new Set(x.bindings.filter((b) => b.status === "ACTIVE").map((b) => b.logical_principal_id));
  const unbound = open.filter((a) => !bound.has(a.agent_principal_id));
  const builds = x.builds ?? [];
  const approvedBuilds = builds.filter((b) => b.review_status === "APPROVED");
  const steps: SetupStep[] = [
    {
      id: "tools",
      title: "Tools",
      detail: x.tools.length ? `${liveTools.length} active of ${x.tools.length}` : "None yet. Register what an agent may use, then activate it.",
      done: liveTools.length > 0,
      tab: "tools",
    },
    {
      id: "blueprint",
      title: "Blueprint",
      detail: x.blueprints.length ? `${approved.length} approved of ${x.blueprints.length}. Someone other than the publisher approves.` : "None yet. Draft a release; a second person approves it.",
      done: approved.length > 0,
      tab: "blueprints",
    },
    {
      id: "agent",
      title: "Agent",
      detail: open.length ? `${open.length} registered` : "None yet. Register one from an approved blueprint.",
      done: open.length > 0,
      tab: "agents",
    },
    {
      id: "workload",
      title: "Runtime binding",
      detail: !open.length ? "After an agent exists." : unbound.length ? `${unbound.length} agent(s) have no binding` : "Every agent has one",
      done: open.length > 0 && unbound.length === 0,
      tab: "workloads",
    },
    {
      id: "activate",
      title: "Checks and activation",
      detail: live.length ? `${live.length} active` : "Attest the four checks on the agent, then activate it.",
      done: live.length > 0,
      tab: "agents",
    },
  ];
  // Where approved builds are required, choosing the software is its own step; elsewhere it appears once builds exist.
  if (x.buildsEnforced || builds.length > 0) {
    steps.splice(1, 0, {
      id: "build",
      title: "Build",
      detail: builds.length ? `${approvedBuilds.length} approved of ${builds.length}. Someone else approves.` : "None yet. Register the software an agent will run; a second person approves it.",
      done: approvedBuilds.length > 0,
      tab: "builds",
    });
  }
  return steps;
}

/* -------------------------------------------------------------------------------------------------- misc */

export const slug = (s: string) =>
  s
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 40);

/** The SPIFFE ID of a company workload: the tenant is part of the path, which is how the cell knows whose it is. */
export function spiffeIdFor(p: { trustDomain: string; tenantId: string; namespace: string; account: string }): string {
  return `spiffe://${p.trustDomain}/t/${p.tenantId}/ns/${slug(p.namespace) || "agents"}/sa/${slug(p.account) || "agent"}`;
}

/** Model providers an agent is commonly run on. Metadata only: it is never part of identity or authority. */
export const MODEL_PROVIDERS: Opt[] = ["Anthropic", "OpenAI", "Google", "Meta", "Mistral", "Self-hosted", "Other"].map((p) => ({ id: p, label: p }));

/** Statements a person can pick when attesting a gate, so the record says what was actually checked. They can still write their own. */
export const GATE_NOTES: Record<AgentGate, Opt[]> = {
  SECURITY_REVIEW: [
    { id: "threat", label: "Threat, model and tool review completed", description: "Reviewed for the agent's risk class." },
    { id: "independent", label: "Reviewed by someone independent of the sponsor, operator and publisher" },
  ],
  KILL_PATH_TESTED: [
    { id: "drill", label: "Suspended and revoked in a drill; the agent stopped", description: "Both paths were tried." },
    { id: "token", label: "Confirmed no new workload token is issued after suspension" },
  ],
  SHORT_LIVED_CREDENTIALS: [
    { id: "workload-token", label: "Uses workload tokens that expire in minutes; no static secrets" },
    { id: "rotation", label: "Credentials rotate automatically and nothing long-lived is stored" },
  ],
  OBSERVABILITY: [
    { id: "trace", label: "Actions trace to the principal, workload, mandate and decision" },
    { id: "audit", label: "Seen in Evidence & audit: receipts and audit records appear for its actions" },
  ],
};
