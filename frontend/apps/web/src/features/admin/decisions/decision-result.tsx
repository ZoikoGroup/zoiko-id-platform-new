"use client";

import type { DecisionOutcome, EvaluationRequest, EvaluationResult } from "@zoiko/api-client";
import { Badge, Code, CopyId, DateTime, JsonView, KeyValues, RiskBadge, type Tone } from "@zoiko/ui";
import { AlertTriangle, CheckCircle2, CloudOff, KeyRound, Stamp, XCircle, type LucideIcon } from "lucide-react";

const OUTCOMES: Record<DecisionOutcome, { tone: Tone; icon: LucideIcon; label: string; meaning: string }> = {
  ALLOW: { tone: "ok", icon: CheckCircle2, label: "Allow", meaning: "The enforcement point may proceed, provided it fulfils every obligation." },
  DENY: { tone: "danger", icon: XCircle, label: "Deny", meaning: "The subject has no authority for this action on this resource in this context." },
  STEP_UP: { tone: "warn", icon: KeyRound, label: "Step-up", meaning: "Allowed only after the subject re-authenticates at a higher assurance level." },
  APPROVAL_REQUIRED: { tone: "info", icon: Stamp, label: "Approval required", meaning: "A transaction-bound approval from a second administrator is needed first." },
  STATE_UNAVAILABLE: {
    tone: "warn",
    icon: CloudOff,
    label: "State unavailable",
    meaning: "Security state could not be read, so the platform refused to decide (fail closed).",
  },
};

export function OutcomeBadge({ result }: { result: EvaluationResult }) {
  const outcome = result.context.outcome;
  const def = outcome ? OUTCOMES[outcome] : undefined;
  if (!def) {
    return (
      <Badge tone={result.decision ? "ok" : "danger"} icon={result.decision ? CheckCircle2 : AlertTriangle} label={`Decision: ${result.decision ? "allow" : "deny"}`}>
        {result.decision ? "Allow" : "Deny"}
      </Badge>
    );
  }
  return (
    <Badge tone={def.tone} icon={def.icon} label={`Outcome: ${def.label}`}>
      {def.label}
    </Badge>
  );
}

export function DecisionResult({ request, result }: { request: EvaluationRequest; result: EvaluationResult }) {
  const c = result.context;
  const def = c.outcome ? OUTCOMES[c.outcome] : undefined;
  const codes = c.reason_codes ?? [];
  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center gap-3" role="status" aria-live="polite">
        <OutcomeBadge result={result} />
        <span className="text-sm text-ink-muted">
          AuthZEN <Code>decision</Code>: <strong className="text-ink">{String(result.decision)}</strong>
        </span>
      </div>
      {def ? <p className="text-sm text-ink-muted">{def.meaning}</p> : null}

      <div>
        <h3 className="text-2xs font-semibold uppercase tracking-wider text-ink-subtle">Reason codes</h3>
        {codes.length ? (
          <ul className="mt-1 flex flex-wrap gap-1">
            {codes.map((code) => (
              <li key={code}>
                <Code>{code}</Code>
              </li>
            ))}
          </ul>
        ) : (
          <p className="mt-1 text-sm text-ink-subtle">None</p>
        )}
      </div>

      <KeyValues
        items={[
          ["Decision ID", c.decision_id ? <CopyId key="d" value={c.decision_id} label="decision" /> : "—"],
          ["Risk class", c.risk_class ? <RiskBadge key="r" risk={c.risk_class} /> : "—"],
          ["Policy release", c.policy_release_id ? <CopyId key="p" value={c.policy_release_id} label="policy release" /> : "—"],
          ["Valid until", <DateTime key="v" value={c.valid_until} utc />],
          ["Relation revision", c.relation_revision ?? "—"],
          ["Security revision", c.security_revision ?? "—"],
        ]}
      />

      <div>
        <h3 className="text-2xs font-semibold uppercase tracking-wider text-ink-subtle">Obligations</h3>
        {c.obligations?.length ? (
          <>
            <p className="mt-1 text-xs text-ink-muted">An enforcement point that cannot fulfil a mandatory obligation must not act.</p>
            <JsonView value={c.obligations} label="Obligations" className="mt-2" />
          </>
        ) : (
          <p className="mt-1 text-sm text-ink-subtle">None</p>
        )}
      </div>

      <details className="rounded-lg border border-border">
        <summary className="cursor-pointer px-4 py-2 text-sm font-medium text-ink">Request and raw response</summary>
        <div className="grid gap-3 p-4 lg:grid-cols-2">
          <JsonView value={request} label="Evaluation request" />
          <JsonView value={result} label="Evaluation response" />
        </div>
      </details>
    </div>
  );
}
