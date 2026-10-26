"use client";

import { ApiError } from "@zoiko/api-client";
import { Banner } from "@zoiko/ui";
import { KeyRound } from "lucide-react";

/** Required synthetic probes per probe-gated step (resilience runbooks PROBES): no partial credit. */
export const PROBES: Record<string, { id: string; label: string }[]> = {
  VALIDATE: [
    { id: "authentication", label: "Passkey sign-in" },
    { id: "authorization_allow", label: "Known ALLOW decision" },
    { id: "authorization_deny", label: "Known DENY decision" },
    { id: "mandate", label: "Mandate reservation" },
    { id: "evidence", label: "Evidence receipt" },
    { id: "revocation", label: "Revocation propagation" },
  ],
  PROBES: [
    { id: "authentication", label: "Passkey sign-in" },
    { id: "authorization_allow", label: "Known ALLOW decision" },
    { id: "authorization_deny", label: "Known DENY decision" },
    { id: "evidence", label: "Evidence receipt" },
  ],
  VERIFY_INTEGRITY: [
    { id: "database_consistency", label: "Database consistency" },
    { id: "evidence_chain", label: "Evidence chain verifies" },
    { id: "erasure_preserved", label: "Crypto-erasure still in force" },
  ],
};

/** The capability each recovery kind needs (operator access catalogue). */
export const KIND_CAPABILITY: Record<string, string> = {
  FAILOVER: "platform.dr.execute",
  FAILBACK: "platform.dr.execute",
  MIGRATION: "platform.migration.execute",
  RESTORE: "platform.migration.execute",
};

/** When the cell refuses for want of a JIT grant, say which one and where to get it. */
export function CapabilityHint({ error, capability }: { error: unknown; capability: string }) {
  if (!(error instanceof ApiError) || error.code !== "OPERATOR_CAPABILITY_REQUIRED") return null;
  return (
    <Banner tone="info" icon={KeyRound} title="Access needed">
      This needs an ACTIVE <span className="font-mono">{capability}</span> grant for exactly this target. Request it under{" "}
      <a href="/requests" className="font-medium text-brand-700 hover:underline">
        My access
      </a>
      ; R3 capabilities need two approvers.
    </Banner>
  );
}

export function seconds(v: number | null | undefined): string {
  if (v === null || v === undefined) return "—";
  if (v < 120) return `${Math.round(v)} s`;
  if (v < 7200) return `${Math.round(v / 60)} min`;
  return `${(v / 3600).toFixed(1)} h`;
}
