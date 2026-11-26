"use client";

import { useQueryClient } from "@tanstack/react-query";
import type { ReviewAdvice, ReviewCase, ReviewFeedback, ReviewReveal } from "@zoiko/api-client";
import { Badge, Banner, Button, Card, Checkbox, DataTable, Dialog, EmptyState, Select, TextField, type Column } from "@zoiko/ui";
import { ClipboardCheck } from "lucide-react";
import { useEffect, useState } from "react";

import { ApiErrorView, QueryView, useCellMutation, useCellQuery } from "@/lib/cell";

import {
  canVerify,
  challengeText,
  checklistDone,
  clock,
  findingsComplete,
  HEARTBEAT_MS,
  iHold,
  listQuery,
  minutesLeft,
  myPart,
  NO_FILTERS,
  OUTCOMES,
  QUEUE_OPTIONS,
  REASONS,
  REVEAL_NOTE_MIN,
  revealReady,
  REVIEW_STATE,
  secondsLeft,
  STATUS_OPTIONS,
  visibleCases,
  type Filters,
} from "./review-logic";

type ReviewOp = "claim" | "release" | "heartbeat" | "findings" | "request-info" | "info-received" | "escalate" | "appeal";
const KEY = ["admin", "developers", "verify-api", "review"];
const when = (iso: string | null): string => (iso ? new Date(iso).toLocaleString("en-GB") : "");

/** Checks that need a person (CR-167): take a case, record each check, propose a result; a DIFFERENT person confirms it. Sensitive evidence stays hidden until you give a reason, and each look is logged. */
export function ReviewCard() {
  const [filters, setFilters] = useState<Filters>(NO_FILTERS);
  const list = useCellQuery([...KEY, "list", filters.status, filters.queue], (api) => api.verifyApi.reviewCases(listQuery(filters)));
  const [open, setOpen] = useState<string | null>(null);
  const me = list.data?.viewer ?? null;
  const columns: Column<ReviewCase>[] = [
    { key: "s", header: "Session", pinned: true, render: (c) => <span className="font-mono text-xs">{c.session_id}</span>, sortValue: (c) => c.session_id },
    { key: "t", header: "Kind", render: (c) => (c.case_type === "APPEAL" ? "Appeal" : "Review") },
    { key: "st", header: "State", render: (c) => <Badge tone={REVIEW_STATE[c.status].tone}>{REVIEW_STATE[c.status].label}</Badge> },
    { key: "q", header: "Queue", render: (c) => (c.queue === "SPECIALIST" ? "Specialist" : "General") },
    { key: "w", header: "With", render: (c) => (iHold(c, me) ? <Badge tone="ok">You</Badge> : c.lease ? <span className="text-xs">{c.lease.reviewer}</span> : null) },
    { key: "h", header: "", render: (c) => (c.on_hold ? <Badge tone="danger">On legal hold</Badge> : c.overdue ? <Badge tone="warn">Overdue</Badge> : null) },
    { key: "d", header: "Due", render: (c) => when(c.sla_due_at), sortValue: (c) => c.sla_due_at },
  ];
  return (
    <>
      <QueryView query={list}>
        {(d) => (
          <Card title="Checks that need a person" description="A reviewer records each check and proposes a result. A second reviewer confirms it. Nothing is decided by one person alone." padded={false}>
            <div className="flex flex-wrap items-end gap-3 border-b border-border p-3">
              <Select label="State" options={STATUS_OPTIONS} value={filters.status} onChange={(v) => setFilters({ ...filters, status: v })} />
              <Select label="Queue" options={QUEUE_OPTIONS} value={filters.queue} onChange={(v) => setFilters({ ...filters, queue: v })} />
              <Checkbox isSelected={filters.mine} onChange={(v) => setFilters({ ...filters, mine: v })}>
                Only the cases I hold
              </Checkbox>
            </div>
            <DataTable<ReviewCase>
              caption="Review cases, oldest first"
              columns={columns}
              rows={visibleCases(d.items, filters, d.viewer)}
              rowKey={(c) => c.case_id}
              onRowAction={(c) => setOpen(c.case_id)}
              empty={
                <EmptyState title="Nothing to review" icon={ClipboardCheck} compact>
                  When a check cannot be decided automatically, it waits here for a person.
                </EmptyState>
              }
            />
          </Card>
        )}
      </QueryView>
      {open ? <ReviewDialog id={open} onClose={() => setOpen(null)} /> : null}
    </>
  );
}

function ReviewDialog({ id, onClose }: { id: string; onClose: () => void }) {
  const qc = useQueryClient();
  const detail = useCellQuery([...KEY, id], (api) => api.verifyApi.reviewCase(id));
  const trail = useCellQuery([...KEY, id, "events"], (api) => api.verifyApi.reviewTrail(id));
  const [outcome, setOutcome] = useState<string>("inconclusive");
  const [reason, setReason] = useState("EVIDENCE_INSUFFICIENT");
  const [note, setNote] = useState("");
  const [why, setWhy] = useState("");
  const [answers, setAnswers] = useState<Record<string, boolean>>({});
  const [template, setTemplate] = useState("");
  const [shown, setShown] = useState<ReviewReveal | null>(null);
  const [asking, setAsking] = useState<string | null>(null);
  const [adviceGiven, setAdviceGiven] = useState<ReviewAdvice | null>(null);
  const [rated, setRated] = useState<ReviewFeedback | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const refresh = () => {
    void qc.invalidateQueries({ queryKey: KEY });
  };
  const c = detail.data;
  const v = c?.case_version;
  const mine = c ? iHold(c, c.viewer) : false;

  // While the person is looking at a case they hold, ask for more time well before the lease ends.
  const beat = useCellMutation<void, unknown>((api) => api.verifyApi.reviewAct(id, "heartbeat"), { onSuccess: refresh });
  useEffect(() => {
    if (!mine) return;
    const t = setInterval(() => beat.mutate(undefined), HEARTBEAT_MS);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- the mutation object changes every render; only the lease matters
  }, [mine, id]);

  // A revealed item counts down and hides itself when its time is up.
  useEffect(() => {
    if (!shown) return;
    const t = setInterval(() => {
      setNow(Date.now());
      if (secondsLeft(shown.visible_until, Date.now()) === 0) {
        setShown(null);
        setAsking(null);
        setWhy("");
      }
    }, 1000);
    return () => clearInterval(t);
  }, [shown]);

  const act = useCellMutation<{ op: ReviewOp; body?: Record<string, unknown> }, unknown>((api, a) => api.verifyApi.reviewAct(id, a.op, { expected_case_version: v, ...a.body }), { onSuccess: refresh });
  const propose = useCellMutation<void, unknown>((api) => api.verifyApi.reviewPropose(id, { expected_case_version: v!, proposed_outcome: outcome, reason_code: reason, evidence_manifest_hash: c!.evidence_manifest_hash }), {
    success: "Proposed. A second reviewer confirms it.",
    onSuccess: refresh,
  });
  const second = useCellMutation<"approve" | "return" | "escalate", unknown>((api, decision) => api.verifyApi.reviewSecond(id, { expected_case_version: v!, decision, challenge_answers: answers, note }), { onSuccess: refresh });
  const reveal = useCellMutation<string, ReviewReveal>((api, evId) => api.verifyApi.reviewReveal(id, evId, why), { onSuccess: (r) => (setShown(r), setNow(Date.now())) });
  const advice = useCellMutation<void, ReviewAdvice>((api) => api.verifyApi.reviewAdvice(id), { onSuccess: (a) => (setAdviceGiven(a), setRated(null)) });
  const rate = useCellMutation<ReviewFeedback, unknown>((api, f) => api.verifyApi.reviewAdviceFeedback(id, adviceGiven!.advice_id, f), { onSuccess: (_r, f) => setRated(f) });
  const error = act.error ?? propose.error ?? second.error ?? reveal.error;
  const part = c ? myPart(c, c.viewer) : "watch";
  const working = c?.status === "INVESTIGATING" || c?.status === "CLAIMED" || c?.status === "SPECIALIST_REVIEW";
  const templates = c ? Object.entries(c.info_templates) : [];

  return (
    <Dialog isOpen onOpenChange={(o) => !o && onClose()} title="Review case" description={c ? REVIEW_STATE[c.status].label : undefined} footer={<Button onPress={onClose}>Close</Button>}>
      {detail.isPending || !c ? (
        <p className="text-sm text-ink-muted">Loading…</p>
      ) : (
        <div className="space-y-4 text-sm">
          <p className="font-mono text-xs">{c.session_id}</p>
          {c.on_hold ? (
            <Banner tone="danger" title="On legal hold">
              A regulator hold stops this case from being decided until Zoiko iD releases it.
            </Banner>
          ) : null}
          {c.lease ? (
            <p className="text-xs text-ink-muted">
              {mine ? "You hold this case" : `Held by ${c.lease.reviewer}`} for about {minutesLeft(c.lease.expires_at, now)} more minutes.
              {mine ? " It is renewed while this window is open." : ""}
            </p>
          ) : null}
          <section aria-label="Evidence" className="space-y-2">
            <h3 className="text-xs font-medium text-ink-muted">Evidence</h3>
            {c.evidence.map((e) => (
              <div key={e.evidence_id} className="flex flex-wrap items-center gap-2 rounded border border-border p-2">
                <span className="font-medium">{e.label}</span>
                <Badge tone={e.classification === "STANDARD" ? "neutral" : "warn"}>{e.classification === "STANDARD" ? "Shown" : e.classification === "SENSITIVE" ? "Hidden" : "Never shown"}</Badge>
                {e.classification === "SENSITIVE" ? (
                  <Button size="sm" variant="secondary" isDisabled={!(mine || part === "second")} onPress={() => (setShown(null), setAsking(e.evidence_id))}>
                    Show for 5 minutes
                  </Button>
                ) : e.summary ? (
                  <span className="font-mono text-xs">{JSON.stringify(e.summary)}</span>
                ) : null}
              </div>
            ))}
          </section>
          {asking ? (
            <div className="space-y-2 rounded border border-border p-3">
              {!shown ? (
                <>
                  <TextField label="Why do you need to see it?" value={why} onChange={setWhy} description={`At least ${REVEAL_NOTE_MIN} characters. Every look is logged with your name.`} />
                  <div className="flex gap-2">
                    <Button size="sm" isDisabled={!revealReady(why)} pending={reveal.isPending} onPress={() => reveal.mutate(asking)}>
                      Show it
                    </Button>
                    <Button size="sm" variant="secondary" onPress={() => setAsking(null)}>
                      Cancel
                    </Button>
                  </div>
                </>
              ) : (
                <>
                  <p className="text-xs text-ink-muted" aria-live="off">
                    Hides itself in {clock(secondsLeft(shown.visible_until, now))}.
                  </p>
                  <pre className="overflow-auto rounded bg-surface-sunken p-2 text-xs">{JSON.stringify(shown.detail, null, 2)}</pre>
                  <Button size="sm" variant="secondary" onPress={() => (setShown(null), setAsking(null), setWhy(""))}>
                    Hide it now
                  </Button>
                </>
              )}
            </div>
          ) : null}
          <section aria-label="Checks" className="space-y-2">
            <h3 className="text-xs font-medium text-ink-muted">Checks</h3>
            {c.required_checks.map((k) => (
              <Select
                key={k}
                label={k.replace(/_/g, " ")}
                isDisabled={!(mine && working)}
                options={[{ id: "", label: "Not recorded" }, { id: "pass", label: "Confirmed" }, { id: "fail", label: "Failed" }, { id: "inconclusive", label: "Cannot tell" }]}
                value={c.findings[k] ?? ""}
                onChange={(val) => val && act.mutate({ op: "findings", body: { findings: { ...c.findings, [k]: val } } })}
              />
            ))}
          </section>
          <div className="flex flex-wrap gap-2">
            {part === "claim" ? (
              <Button size="sm" pending={act.isPending} isDisabled={c.on_hold} onPress={() => act.mutate({ op: "claim" })}>
                Take this case
              </Button>
            ) : null}
            {mine && working ? (
              <Button size="sm" variant="secondary" onPress={() => act.mutate({ op: "release" })}>
                Give it back
              </Button>
            ) : null}
            {part === "applicant" ? (
              <Button size="sm" variant="secondary" isDisabled={c.on_hold} onPress={() => act.mutate({ op: "info-received" })}>
                The person has sent it
              </Button>
            ) : null}
            {c.status === "APPROVED_FINAL" && !c.appeal_case_id && c.final_outcome === "not_verified" ? (
              <Button size="sm" variant="secondary" onPress={() => act.mutate({ op: "appeal", body: { note } })}>
                Appeal this result
              </Button>
            ) : null}
          </div>
          {mine && working ? (
            <section aria-label="Ask or escalate" className="space-y-2">
              <h3 className="text-xs font-medium text-ink-muted">Not enough to decide?</h3>
              <div className="flex flex-wrap items-end gap-2">
                <Select label="Ask the person for" options={[{ id: "", label: "Choose…" }, ...templates.map(([k, text]) => ({ id: k, label: text }))]} value={template} onChange={setTemplate} />
                <Button size="sm" variant="secondary" isDisabled={!template || c.on_hold} pending={act.isPending} onPress={() => act.mutate({ op: "request-info", body: { template } })}>
                  Ask
                </Button>
              </div>
              {c.queue !== "SPECIALIST" ? (
                <div className="flex flex-wrap items-end gap-2">
                  <TextField label="Why a specialist should look" value={note} onChange={setNote} />
                  <Button size="sm" variant="secondary" isDisabled={!note.trim() || c.on_hold} onPress={() => act.mutate({ op: "escalate", body: { note } })}>
                    Escalate
                  </Button>
                </div>
              ) : null}
            </section>
          ) : null}
          {mine && working ? (
            <section aria-label="Propose" className="space-y-2">
              <h3 className="text-xs font-medium text-ink-muted">Propose a result</h3>
              <Select label="Result" options={OUTCOMES.filter((o) => o.id !== "verified" || canVerify(c)).map((o) => ({ id: o.id, label: o.label }))} value={outcome} onChange={setOutcome} />
              <Select label="Reason" options={REASONS} value={reason} onChange={setReason} />
              <Button size="sm" isDisabled={!findingsComplete(c) || c.on_hold} pending={propose.isPending} onPress={() => propose.mutate(undefined)}>
                Propose
              </Button>
              {!findingsComplete(c) ? <p className="text-xs text-ink-muted">Record every check first.</p> : null}
            </section>
          ) : null}
          {part === "second" && c.proposal ? (
            <section aria-label="Second review" className="space-y-2">
              <Banner tone="info" title={`Proposed: ${c.proposal.outcome.replace(/_/g, " ")}`}>
                By {c.proposal.reviewer === c.viewer ? "you" : c.proposal.reviewer}.{c.proposal.reviewer === c.viewer ? " You cannot confirm your own proposal: another reviewer must." : ""}
              </Banner>
              {c.proposal.reviewer !== c.viewer ? (
                <>
                  <div className="space-y-1" role="group" aria-label="Before you confirm">
                    {c.challenges.map((k) => (
                      <Checkbox key={k} isSelected={answers[k] === true} onChange={(on) => setAnswers({ ...answers, [k]: on })}>
                        {challengeText(k)}
                      </Checkbox>
                    ))}
                  </div>
                  <TextField label="Your note (needed to send it back)" value={note} onChange={setNote} />
                  <div className="flex flex-wrap gap-2">
                    <Button size="sm" pending={second.isPending} isDisabled={c.on_hold || !checklistDone(c.challenges, answers)} onPress={() => second.mutate("approve")}>
                      Confirm
                    </Button>
                    <Button size="sm" variant="secondary" isDisabled={!note.trim()} onPress={() => second.mutate("return")}>
                      Send back
                    </Button>
                    <Button size="sm" variant="secondary" isDisabled={!note.trim() || c.on_hold} onPress={() => second.mutate("escalate")}>
                      Escalate
                    </Button>
                  </div>
                </>
              ) : null}
            </section>
          ) : null}
          {mine && working ? (
            <section aria-label="Assistant" className="space-y-2">
              <Button size="sm" variant="secondary" pending={advice.isPending} onPress={() => advice.mutate(undefined)}>
                Ask the assistant
              </Button>
              {adviceGiven ? (
                <>
                  <Banner tone="info" title={adviceGiven.status === "ADVICE" ? "Suggestion (you decide)" : "The assistant did not advise"}>
                    {adviceGiven.advice?.summary ?? adviceGiven.abstain_reason ?? "No suggestion."}
                  </Banner>
                  {adviceGiven.status === "ADVICE" ? (
                    <div className="flex flex-wrap items-center gap-2" role="group" aria-label="Was it useful?">
                      <span className="text-xs text-ink-muted">{rated ? `Thanks: ${rated}.` : "Was it useful?"}</span>
                      {!rated ? (
                        <>
                          <Button size="sm" variant="secondary" onPress={() => rate.mutate("helpful")}>
                            Helpful
                          </Button>
                          <Button size="sm" variant="secondary" onPress={() => rate.mutate("incorrect")}>
                            Wrong
                          </Button>
                          <Button size="sm" variant="secondary" onPress={() => rate.mutate("ignored")}>
                            Not used
                          </Button>
                        </>
                      ) : null}
                    </div>
                  ) : null}
                </>
              ) : null}
            </section>
          ) : null}
          {trail.data ? (
            <section aria-label="History" className="space-y-1">
              <h3 className="text-xs font-medium text-ink-muted">History {trail.data.chain_intact ? "" : "(the record shows a break)"}</h3>
              <ul className="space-y-1 text-xs">
                {trail.data.events.map((e) => (
                  <li key={e.seq}>
                    {when(e.at)}: {e.type.toLowerCase().replace(/_/g, " ")} by {e.actor}
                  </li>
                ))}
              </ul>
            </section>
          ) : null}
          {error ? <ApiErrorView error={error} title="That was not recorded" onRetry={() => (act.reset(), propose.reset(), second.reset(), reveal.reset())} /> : null}
        </div>
      )}
    </Dialog>
  );
}
