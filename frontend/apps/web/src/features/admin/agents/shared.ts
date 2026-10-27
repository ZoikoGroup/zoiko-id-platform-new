import type { AgentGate } from "@zoiko/api-client";

export const AGENTS_KEY = ["admin", "agents"];
export const BLUEPRINTS_KEY = ["admin", "agents", "blueprints"];
export const TOOLS_KEY = ["admin", "agents", "tools"];

export const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
export const DIGEST_RE = /^sha-?256:[0-9a-f]{64}$/;

/** The four activation gates a person must attest (ZID-MACHINE-001 §12.1). The other six are checked by the platform. */
export const GATES: { id: AgentGate; label: string; help: string }[] = [
  { id: "SECURITY_REVIEW", label: "Security review passed", help: "Threat, model and tool review for the agent's risk class. Must be done by someone who is not its sponsor, operator or publisher." },
  { id: "KILL_PATH_TESTED", label: "Kill path tested", help: "Suspending and revoking this agent was tried and stopped it." },
  { id: "SHORT_LIVED_CREDENTIALS", label: "Short-lived credentials", help: "It authenticates with short-lived workload credentials, not static secrets." },
  { id: "OBSERVABILITY", label: "Actions are traceable", help: "Its actions can be traced to the principal, workload, mandate and decision." },
];

/** What the platform checks itself, with the reason code it reports while blocking. */
export const PLATFORM_CHECKS: { code: string; label: string }[] = [
  { code: "AGENT_SPONSOR_REQUIRED", label: "Accountable sponsor and owning organization verified" },
  { code: "AGENT_BLUEPRINT_NOT_APPROVED", label: "Blueprint release approved" },
  { code: "AGENT_TOOL_MANIFEST_REQUIRED", label: "Maximum toolset registered" },
  { code: "AGENT_RUNTIME_BINDING_REQUIRED", label: "A live runtime (workload) binding exists" },
];

export const BLOCKER_TEXT: Record<string, string> = {
  AGENT_SPONSOR_REQUIRED: "A verified, active sponsor and a registered owning organization are required.",
  AGENT_BLUEPRINT_NOT_APPROVED: "The blueprint release is not approved (or was revoked).",
  AGENT_TOOL_MANIFEST_REQUIRED: "Choose the tools this agent may ever use.",
  AGENT_RUNTIME_BINDING_REQUIRED: "Bind a runtime workload identity under Workload identity first.",
  AGENT_SECURITY_REVIEW_REQUIRED: "An independent security review has not been attested.",
  AGENT_KILL_PATH_UNTESTED: "The kill path has not been attested as tested.",
  AGENT_CREDENTIALS_NOT_SHORT_LIVED: "Short-lived credentials have not been attested.",
  AGENT_OBSERVABILITY_REQUIRED: "Traceability of its actions has not been attested.",
};

export function splitList(value: string): string[] {
  return value
    .split(/[\s,]+/)
    .map((s) => s.trim())
    .filter(Boolean);
}
