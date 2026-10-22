"use client";

import { Badge, DateTime } from "@zoiko/ui";
import type { ReactNode } from "react";

export const PRIVACY_KEY = ["admin", "privacy"];

export const RIGHTS_TYPES = [
  { id: "ACCESS", label: "Access / know" },
  { id: "PORTABILITY", label: "Portability" },
  { id: "RECTIFICATION", label: "Rectification" },
  { id: "ERASURE", label: "Erasure" },
  { id: "RESTRICTION", label: "Restriction" },
  { id: "OBJECTION", label: "Objection" },
  { id: "OPT_OUT_SALE_SHARING", label: "Opt out of sale/sharing" },
  { id: "LIMIT_SENSITIVE_PI", label: "Limit sensitive PI" },
  { id: "ADMT_ACCESS", label: "Automated decision: access" },
  { id: "ADMT_OPT_OUT", label: "Automated decision: opt out" },
  { id: "HUMAN_REVIEW", label: "Human review of a decision" },
  { id: "COMPLAINT", label: "Complaint" },
];

export function rightsLabel(type: string): string {
  return RIGHTS_TYPES.find((t) => t.id === type)?.label ?? type;
}

/** Render an arbitrary row value (ids, arrays, times, booleans) compactly. */
export function cellValue(v: unknown): ReactNode {
  if (v === null || v === undefined || v === "") return <span className="text-ink-subtle">—</span>;
  if (typeof v === "boolean") return v ? "Yes" : "No";
  if (Array.isArray(v)) return <span className="font-mono text-2xs">{v.join(", ") || "—"}</span>;
  if (typeof v === "string" && /^\d{4}-\d{2}-\d{2}T/.test(v)) return <DateTime value={v} />;
  return <span className="text-xs">{String(v)}</span>;
}

export function DeadlineBadge({ deadline, source, consumed }: { deadline: string | null; source: string; consumed: number | null }) {
  if (!deadline) return <Badge tone="warn">{source === "COUNSEL_PENDING" ? "Deadline: counsel pending" : "No deadline"}</Badge>;
  const tone = consumed !== null && consumed >= 0.9 ? "danger" : consumed !== null && consumed >= 0.75 ? "warn" : "neutral";
  return (
    <span className="inline-flex items-center gap-1.5">
      <DateTime value={deadline} style="date" />
      {consumed !== null ? <Badge tone={tone}>{Math.round(consumed * 100)}%</Badge> : null}
    </span>
  );
}
