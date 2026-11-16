"use client";

import { ApiError, type SignalOutcome, type SignalReview } from "@zoiko/api-client";
import { Badge, Banner, Button, Card, DataTable, DateTime, Dialog, EmptyState, PageHeader, Select, StatusBadge, TextField, type Column } from "@zoiko/ui";
import { Flag } from "lucide-react";
import { useState, type ReactNode } from "react";

import { ApiErrorView, QueryView, useCellMutation, useCellQuery } from "@/lib/op";

import { ageText, OUTCOME_HELP, OUTCOME_LABEL, shortHash, SIGNAL_ERROR_HELP, signalSummary, SOURCE_LABEL, windowText } from "./signals-format";

const SIGNALS_KEY = ["operator", "signals"];
const TIER_TONE = { LOW: "neutral", ELEVATED: "warn", HIGH: "danger" } as const;
const OUTCOMES: SignalOutcome[] = ["CLEARED_BY_REVIEWER", "CONFIRMED_RISK", "ESCALATED"];

function SignalError({ error }: { error: unknown }) {
  if (error instanceof ApiError && SIGNAL_ERROR_HELP[error.code]) {
    return (
      <Banner tone="danger" title="Not saved" live="assertive">
        {SIGNAL_ERROR_HELP[error.code]} <span className="block text-xs">{error.message}</span>
      </Banner>
    );
  }
  return <ApiErrorView error={error} />;
}

function Hash({ value }: { value: string }) {
  return (
    <span className="font-mono text-xs text-ink-muted" title={value}>
      {shortHash(value)}
    </span>
  );
}

function NotifyBadge({ state }: { state: SignalReview["notify_state"] }) {
  if (state === "FAILED") {
    return (
      <Badge tone="danger" label="Notification failed: no one has been told about this signal">
        Notification failed
      </Badge>
    );
  }
  return <StatusBadge state={state} kind="Notification" />;
}

function Detail({ s }: { s: SignalReview }) {
  const d = s.detail;
  const rows: [string, ReactNode][] =
    s.source === "VELOCITY"
      ? [
          ["Rule", d.rule ?? s.rule],
          ["Dimension", d.dimension ?? "-"],
          ["Action", d.action ?? "-"],
          ["Count seen", String(d.count ?? "-")],
          ["Threshold", String(d.threshold ?? "-")],
          ["Window", windowText(d.window_seconds)],
          ["Key hash", d.key_hash ? <Hash value={d.key_hash} /> : "-"],
        ]
      : s.source === "IDENTITY_SIGNAL"
        ? [
            ["Rule", s.rule],
            ["People in the group", String(d.people ?? "-")],
            ["Kinds of shared detail", String(d.kinds_shared ?? "-")],
            ["Shared identifiers", String(d.identifiers_shared ?? "-")],
          ]
        : [
          ["Provider", d.provider ?? "-"],
          ["Queried", d.queried_at ? <DateTime value={d.queried_at} /> : "-"],
          ["Provider outcome", d.outcome ?? "-"],
          ["Hits", String(d.hit_count ?? 0)],
          ["Severity", d.severity ?? "-"],
          ["Provider rationale", d.rationale ?? "-"],
        ];
  return (
    <section aria-label="Signal detail" className="space-y-2">
      <p className="text-sm">{signalSummary(s)}</p>
      <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1 text-sm">
        {rows.map(([k, v]) => (
          <div key={k} className="contents">
            <dt className="text-ink-muted">{k}</dt>
            <dd>{v}</dd>
          </div>
        ))}
      </dl>
      {s.source === "ADVERSE_MEDIA" && d.reasons?.length ? (
        <div>
          <p className="text-sm font-medium">Reasons given by the provider</p>
          <ul className="list-disc pl-5 text-sm">
            {d.reasons.map((r, i) => (
              <li key={i}>{r}</li>
            ))}
          </ul>
        </div>
      ) : null}
      {s.status === "CHALLENGED" ? <Banner tone="info">This signal has been challenged and is still open until someone resolves it.</Banner> : null}
    </section>
  );
}

function ReviewDialog({ signal, onClose }: { signal: SignalReview | null; onClose: () => void }) {
  const [statement, setStatement] = useState("");
  const [challengeRef, setChallengeRef] = useState("");
  const [outcome, setOutcome] = useState<SignalOutcome>("CLEARED_BY_REVIEWER");
  const [rationale, setRationale] = useState("");
  const [resolveRef, setResolveRef] = useState("");
  const done = () => {
    setStatement("");
    setChallengeRef("");
    setRationale("");
    setResolveRef("");
    onClose();
  };
  const challenge = useCellMutation((api, s: SignalReview) => api.challengeSignal(s.review_id, { statement: statement.trim(), evidence_ref: challengeRef.trim() }), {
    invalidate: [SIGNALS_KEY],
    success: "Challenge recorded: the signal stays open",
    onSuccess: done,
  });
  const resolve = useCellMutation((api, s: SignalReview) => api.resolveSignal(s.review_id, { outcome, rationale: rationale.trim(), evidence_ref: resolveRef.trim() }), {
    invalidate: [SIGNALS_KEY],
    success: "Signal resolved",
    onSuccess: done,
  });
  return (
    <Dialog
      isOpen={signal !== null}
      onOpenChange={(o) => !o && onClose()}
      title="Review the signal"
      size="lg"
      footer={
        <Button variant="secondary" onPress={onClose}>
          Close
        </Button>
      }
    >
      {signal ? (
        <div className="space-y-6">
          <Detail s={signal} />
          <form
            aria-label="Challenge"
            className="space-y-3 border-t border-line pt-4"
            onSubmit={(e) => {
              e.preventDefault();
              challenge.mutate(signal);
            }}
          >
            <h3 className="text-sm font-semibold">Challenge</h3>
            <p className="text-sm text-ink-muted">Question the signal without closing it. It stays in the queue as challenged.</p>
            <TextField label="Statement" description="20 to 2000 characters" value={statement} onChange={setStatement} isRequired multiline rows={3} />
            <TextField label="Evidence reference" value={challengeRef} onChange={setChallengeRef} isRequired />
            {challenge.error ? <SignalError error={challenge.error} /> : null}
            <Button type="submit" variant="secondary" pending={challenge.isPending} isDisabled={statement.trim().length < 20 || !challengeRef.trim()}>
              Record challenge
            </Button>
          </form>
          <form
            aria-label="Resolve"
            className="space-y-3 border-t border-line pt-4"
            onSubmit={(e) => {
              e.preventDefault();
              resolve.mutate(signal);
            }}
          >
            <h3 className="text-sm font-semibold">Resolve</h3>
            <p className="text-sm text-ink-muted">Close the signal with a ruling. A ruling is a record for people; it does not block or approve anything.</p>
            <Select
              label="Outcome"
              value={outcome}
              onChange={(v) => setOutcome(OUTCOMES.find((o) => o === v) ?? "CLEARED_BY_REVIEWER")}
              options={OUTCOMES.map((o) => ({ id: o, label: OUTCOME_LABEL[o], description: OUTCOME_HELP[o] }))}
            />
            <TextField label="Rationale" description="At least 20 characters" value={rationale} onChange={setRationale} isRequired multiline rows={3} />
            <TextField label="Evidence reference" value={resolveRef} onChange={setResolveRef} isRequired />
            {resolve.error ? <SignalError error={resolve.error} /> : null}
            <Button type="submit" pending={resolve.isPending} isDisabled={rationale.trim().length < 20 || !resolveRef.trim()}>
              Resolve signal
            </Button>
          </form>
        </div>
      ) : null}
    </Dialog>
  );
}

export function SignalsPage() {
  const signals = useCellQuery(SIGNALS_KEY, (api) => api.signalReviews());
  const [reviewing, setReviewing] = useState<SignalReview | null>(null);
  const columns: Column<SignalReview>[] = [
    {
      key: "tier",
      header: "Risk",
      pinned: true,
      render: (s) => (
        <Badge tone={TIER_TONE[s.risk_tier] ?? "neutral"} label={`Risk tier: ${s.risk_tier}`}>
          {s.risk_tier.charAt(0) + s.risk_tier.slice(1).toLowerCase()}
        </Badge>
      ),
    },
    { key: "source", header: "Source", render: (s) => SOURCE_LABEL[s.source] },
    { key: "rule", header: "Rule", render: (s) => <span className="font-mono text-2xs">{s.rule}</span> },
    { key: "summary", header: "What was seen", render: (s) => signalSummary(s) },
    { key: "status", header: "Status", render: (s) => <StatusBadge state={s.status} kind="Signal status" /> },
    { key: "notify", header: "Notification", render: (s) => <NotifyBadge state={s.notify_state} /> },
    { key: "age", header: "Age", render: (s) => <span title={s.created_at}>{ageText(s.created_at)}</span> },
    {
      key: "actions",
      header: "Actions",
      align: "end",
      render: (s) => (
        <Button size="xs" onPress={() => setReviewing(s)} aria-label={`Review signal ${s.rule}`}>
          Review
        </Button>
      ),
    },
  ];
  return (
    <>
      <PageHeader
        title="Signals"
        description="A signal is a flag for a person to look at. It never blocks or approves anything: your ruling is a record, and any action is taken by people through the normal approvals."
      />
      <Card padded={false} title="Waiting for a person">
        <QueryView query={signals}>
          {(d) => (
            <>
              {d.items.some((s) => s.notify_state === "FAILED") ? (
                <Banner tone="warn" title="Some notifications failed" live="polite">
                  No one was told about the items marked Notification failed. They are in this queue so they are not missed.
                </Banner>
              ) : null}
              <DataTable
                caption="Signals"
                columns={columns}
                rows={d.items}
                rowKey={(s) => s.review_id}
                empty={<EmptyState icon={Flag} title="Nothing is waiting">Every signal has been looked at.</EmptyState>}
              />
            </>
          )}
        </QueryView>
      </Card>
      <ReviewDialog signal={reviewing} onClose={() => setReviewing(null)} />
    </>
  );
}
