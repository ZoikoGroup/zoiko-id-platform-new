import type { AgentGate, AgentItem, BlueprintRelease, ToolItem, ToolSideEffect, WorkloadBindingView } from "@zoiko/api-client";

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
  agent: ["machine.agent.manage"],
  activate: ["machine.agent.activate"],
  workload: ["machine.workload.manage"],
} as const;

/* ------------------------------------------------------------------------------------------ setup guide */

export type SetupStep = { id: string; title: string; detail: string; done: boolean; tab: "tools" | "blueprints" | "agents" | "workloads" };

/** The order to set an agent up in, and where each step stands. The first step not done is the one to do next. */
export function setupSteps(x: { tools: ToolItem[]; blueprints: BlueprintRelease[]; agents: AgentItem[]; bindings: WorkloadBindingView[] }): SetupStep[] {
  const liveTools = x.tools.filter((t) => t.status === "ACTIVE");
  const approved = x.blueprints.filter((b) => b.review_status === "APPROVED");
  const live = x.agents.filter((a) => a.status === "ACTIVE");
  const open = x.agents.filter((a) => a.status !== "REVOKED" && a.status !== "RETIRED");
  const bound = new Set(x.bindings.filter((b) => b.status === "ACTIVE").map((b) => b.logical_principal_id));
  const unbound = open.filter((a) => !bound.has(a.agent_principal_id));
  return [
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
