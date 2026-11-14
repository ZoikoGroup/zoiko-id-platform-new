"use client";

import type { ReadinessDryRun, ReadinessSetup } from "@zoiko/api-client";
import { Badge, Banner, Card } from "@zoiko/ui";
import { Check, CircleAlert, CircleDashed, Minus } from "lucide-react";

import { RULE_TEXT, STAGES, severityTone, stageIndex, type StageId, type StepState, type TimelineStep } from "./logic";

/** WF-AG-01 stepper: five stages as real buttons, the current one marked, a stage with a problem or unsaved edits said in words. */
export function AgentSetupStepper({
  current,
  furthest,
  dirty = false,
  invalid = [],
  onGo,
}: {
  current: StageId;
  furthest: StageId;
  dirty?: boolean;
  invalid?: StageId[];
  onGo: (stage: StageId) => void;
}) {
  return (
    <nav aria-label="Setup stages">
      <ol className="grid grid-cols-5 gap-2">
        {STAGES.map((s, i) => {
          const reachable = i <= stageIndex(furthest);
          const isCurrent = s.id === current;
          const done = i < stageIndex(current) || (reachable && !isCurrent && i < stageIndex(furthest));
          const bad = invalid.includes(s.id);
          return (
            <li key={s.id} className="min-w-0">
              <button
                type="button"
                disabled={!reachable}
                aria-current={isCurrent ? "step" : undefined}
                onClick={() => onGo(s.id)}
                className={`flex min-h-11 w-full min-w-11 flex-col items-center justify-center gap-0.5 rounded-card border px-1 py-1.5 text-xs font-medium outline-none focus-visible:ring-2 focus-visible:ring-brand-500 disabled:cursor-not-allowed disabled:opacity-50 sm:flex-row sm:gap-2 sm:px-3 sm:text-sm ${
                  isCurrent ? "border-brand-500 bg-brand-50/40 text-ink" : "border-border bg-surface text-ink-muted hover:bg-hover"
                }`}
              >
                <span
                  aria-hidden
                  className={`flex size-6 shrink-0 items-center justify-center rounded-full text-xs font-semibold ${bad ? "bg-danger-bg text-danger-fg" : done ? "bg-ok-bg text-ok-fg" : "bg-sunken text-ink-muted"}`}
                >
                  {bad ? <CircleAlert className="size-3.5" /> : done ? <Check className="size-3.5" /> : i + 1}
                </span>
                <span className="truncate">{s.short}</span>
                <span className="sr-only">
                  {isCurrent ? " (current" : done ? " (done" : reachable ? " (available" : " (locked until earlier stages pass"}
                  {bad ? ", has a problem" : ""}
                  {isCurrent && dirty ? ", unsaved changes" : ""})
                </span>
              </button>
            </li>
          );
        })}
      </ol>
    </nav>
  );
}

const TOOL_TEXT: Record<string, string> = {
  read_mandate_metadata: "Read mandate metadata",
  read_agent_identity_status: "Read agent identity status",
  create_access_review_proposal: "Create an access review proposal",
};
const BLOCKED_TEXT: Record<string, string> = {
  grant_role: "Grant a role",
  revoke_credential: "Revoke a credential",
  change_mandate: "Change a mandate",
  edit_policy: "Edit a policy",
  spend: "Spend money",
};

/** The authoritative, read-only result of policy: never editable, and every denial is written out. Not the same element as the editable scope. */
export function EffectiveScopeSummary({ setup }: { setup: ReadinessSetup | null }) {
  return (
    <Card title="Effective controls" description="What the platform will enforce. Read only; the server computes it." as="section">
      <div className="space-y-4 text-sm">
        {setup ? (
          <>
            <p>
              <Badge tone={setup.status === "APPROVED" ? "ok" : "warn"}>{setup.status === "APPROVED" ? "Approved scope" : "Not approved yet"}</Badge>
              <span className="ms-2 text-ink-muted">{setup.status === "APPROVED" ? "Signed by an approver." : "This is the requested scope. Nothing runs until it is approved."}</span>
            </p>
            <dl className="grid grid-cols-2 gap-x-4 gap-y-2">
              <dt className="text-ink-muted">Purpose</dt>
              <dd>Access posture review</dd>
              <dt className="text-ink-muted">Region</dt>
              <dd>{setup.scope.home_region}</dd>
              <dt className="text-ink-muted">Resource kinds</dt>
              <dd>{setup.scope.resource_kinds.join(", ")}</dd>
              <dt className="text-ink-muted">Records per run</dt>
              <dd>{setup.scope.max_records}</dd>
              <dt className="text-ink-muted">Warning window</dt>
              <dd>{setup.scope.warning_window_days} days</dd>
            </dl>
          </>
        ) : (
          <p className="text-ink-muted">Tenant and region are set from your signed-in workspace. They appear here once the draft is saved.</p>
        )}
        <div>
          <h3 className="text-xs font-semibold uppercase tracking-wider text-ink-subtle">Allowed</h3>
          <ul className="mt-1 space-y-1">
            {(setup?.permitted_tools ?? Object.keys(TOOL_TEXT)).map((t) => (
              <li key={t} className="flex items-start gap-2">
                <Check aria-hidden className="mt-0.5 size-4 shrink-0 text-ok-fg" />
                <span>
                  <span className="font-medium">Allowed:</span> {TOOL_TEXT[t] ?? t}
                </span>
              </li>
            ))}
          </ul>
        </div>
        <div>
          <h3 className="text-xs font-semibold uppercase tracking-wider text-ink-subtle">Never allowed</h3>
          <ul className="mt-1 space-y-1">
            {(setup?.blocked ?? Object.keys(BLOCKED_TEXT)).map((b) => (
              <li key={b} className="flex items-start gap-2">
                <Minus aria-hidden className="mt-0.5 size-4 shrink-0 text-danger-fg" />
                <span>
                  <span className="font-medium">Denied:</span> {BLOCKED_TEXT[b] ?? b}
                </span>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </Card>
  );
}

/** Bounded first-party worker only in v1: shown as facts, not as choices. The AI explanation is the one opt-in, off by default. */
export function ExecutionModeCard({
  aiExplanation = false,
  onAiExplanation,
  error,
}: {
  aiExplanation?: boolean;
  /** Without it the flag is shown read-only. */
  onAiExplanation?: (on: boolean) => void;
  error?: string;
}) {
  return (
    <Card title="Execution mode" description="Fixed in this release, except the optional AI explanation.">
      <dl className="grid gap-x-6 gap-y-3 text-sm sm:grid-cols-2">
        <div>
          <dt className="text-ink-muted">Worker</dt>
          <dd className="font-medium">Managed reference worker</dd>
        </div>
        <div>
          <dt className="text-ink-muted">Runs</dt>
          <dd className="font-medium">On demand, started by a person</dd>
          <dd className="text-xs text-ink-subtle">Scheduled runs are not available.</dd>
        </div>
        <div>
          <dt className="text-ink-muted">Connector</dt>
          <dd className="font-medium">Approved internal metadata adapter</dd>
        </div>
        <div>
          <dt className="text-ink-muted">AI explanation</dt>
          <dd className="font-medium">{aiExplanation ? "On" : "Off"}</dd>
          <dd className="text-xs text-ink-subtle">Results come from fixed rules only. An explanation never changes them.</dd>
        </div>
      </dl>
      {onAiExplanation ? (
        <div className="mt-4 border-t border-border pt-4">
          <label className="flex min-h-11 items-center gap-2 text-sm font-medium">
            <input
              type="checkbox"
              name="ai_explanation"
              checked={aiExplanation}
              aria-invalid={error ? true : undefined}
              aria-describedby={error ? "ai-explanation-help ai-explanation-error" : "ai-explanation-help"}
              onChange={(e) => onAiExplanation(e.target.checked)}
              className="size-5"
            />
            Add a plain-language AI explanation to each run
          </label>
          <p id="ai-explanation-help" className="text-xs text-ink-subtle">
            Off by default. Only rule codes and counts are sent, never names, ids or documents. Text that does not match the result is replaced by fixed text. The approver signs this choice.
          </p>
          {error ? (
            <p id="ai-explanation-error" className="text-xs font-medium text-danger-fg">
              {error}
            </p>
          ) : null}
        </div>
      ) : null}
    </Card>
  );
}

/** Synthetic data label and the no-live-write notice are always part of the panel, passed or failed. */
export function AgentDryRunResults({ result }: { result: ReadinessDryRun }) {
  return (
    <section aria-label="Dry run results" className="space-y-3">
      <Banner tone="info" title="Synthetic data. No live write.">
        This test used invented records in a sandbox. No live case, proposal, run or evidence was created, and no access changed.
        {result.live_writes !== 0 ? <strong> The server reported {result.live_writes} live writes. Treat this as a test failure and tell security.</strong> : null}
      </Banner>
      <p role="status" className="flex flex-wrap items-center gap-2 text-sm">
        <Badge tone={result.passed ? "ok" : "danger"}>{result.passed ? "Dry run passed" : "Dry run did not pass"}</Badge>
        <span className="text-ink-muted">
          Fixture {result.fixture} · policy {result.policy_version} · rules {result.rule_set_version} · {result.findings.length} findings · {result.unknown} unknown
        </span>
      </p>
      <p className="text-xs text-ink-subtle">
        Rules covered: {result.rules_covered.length ? result.rules_covered.join(", ") : "none"}. Result digest <code className="break-all font-mono">{result.result_digest}</code>
      </p>
      {result.findings.length ? (
        <ul className="divide-y divide-border rounded-card border border-border">
          {result.findings.map((f, i) => (
            <li key={`${f.target_ref}-${f.rule_id}-${i}`} className="flex flex-wrap items-center gap-2 px-3 py-2 text-sm">
              <span className="font-medium">
                {f.rule_id} {RULE_TEXT[f.rule_id] ?? f.finding_code}
              </span>
              <Badge tone={severityTone(f.severity)}>Severity: {f.severity}</Badge>
              <span className="text-xs text-ink-muted">Synthetic record {f.target_ref}</span>
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}

const STEP_TEXT: Record<StepState, string> = { done: "Done", pending: "Not reached", stopped: "Stopped here", unverified: "Proof missing" };

/** WF-AG-03 timeline: stable order, and a step whose proof does not verify says so instead of showing green. */
export function AgentEvidenceTimeline({ steps }: { steps: TimelineStep[] }) {
  return (
    <ol aria-label="Run timeline" className="grid gap-2 sm:grid-cols-2 lg:grid-cols-7">
      {steps.map((s, i) => (
        <li key={s.id} className="flex items-center gap-2 rounded-card border border-border px-3 py-2 text-sm lg:flex-col lg:items-start">
          <span
            aria-hidden
            className={`flex size-6 shrink-0 items-center justify-center rounded-full text-xs ${
              s.state === "done" ? "bg-ok-bg text-ok-fg" : s.state === "unverified" || s.state === "stopped" ? "bg-warn-bg text-warn-fg" : "bg-sunken text-ink-muted"
            }`}
          >
            {s.state === "done" ? <Check className="size-3.5" /> : s.state === "pending" ? <CircleDashed className="size-3.5" /> : <CircleAlert className="size-3.5" />}
          </span>
          <span className="min-w-0">
            <span className="block font-medium">
              {i + 1}. {s.label}
            </span>
            <span className="block text-xs text-ink-muted">{STEP_TEXT[s.state]}</span>
          </span>
        </li>
      ))}
    </ol>
  );
}
