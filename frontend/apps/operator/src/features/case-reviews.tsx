"use client";

import { ApiError, type ScreeningCaseDetail, type ScreeningReplay } from "@zoiko/api-client";
import { Badge, Banner, Button, Card, DataTable, EmptyState, PageHeader, Select, TextField, type Column } from "@zoiko/ui";
import { useState } from "react";

import { ApiErrorView, QueryView, useCellMutation, useCellQuery } from "@/lib/op";

export type CaseKind = "SCREENING" | "KYB";

export const KIND_LABEL: Record<CaseKind, string> = { SCREENING: "Company screening", KYB: "Company verification (KYB)" };

export const SCREENING_OUTCOMES = ["VERIFIED", "REJECTED", "REVIEW_REQUIRED"] as const;
export const KYB_OUTCOMES = ["VERIFIED", "REJECTED", "NEEDS_REVIEW"] as const;
export type Outcome = (typeof SCREENING_OUTCOMES)[number] | (typeof KYB_OUTCOMES)[number];

export const OUTCOME_LABEL: Record<Outcome, string> = {
  VERIFIED: "Verified",
  REJECTED: "Rejected",
  REVIEW_REQUIRED: "Keep for review",
  NEEDS_REVIEW: "Keep for review",
};

export const OUTCOME_HELP: Record<Outcome, string> = {
  VERIFIED: "You looked and the evidence supports it. This is your ruling, with your reason on record.",
  REJECTED: "You looked and it does not hold up. The applicant can appeal and a person looks again.",
  REVIEW_REQUIRED: "You cannot decide yet. It stays with a person.",
  NEEDS_REVIEW: "You cannot decide yet. It stays with a person.",
};

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export function isCaseId(value: string): boolean {
  return UUID.test(value.trim());
}

/** A ruling needs a reason of at least ten characters, as the cell requires. Only a ruling that verifies can confirm the delegated mandate. */
export function rulingReady(rationale: string): boolean {
  return rationale.trim().length >= 10;
}

export function mandateAllowed(kind: CaseKind, outcome: Outcome): boolean {
  return kind === "SCREENING" && outcome === "VERIFIED";
}

const ERROR_HELP: Record<string, string> = {
  CASE_NOT_FOUND: "No case with this id.",
  PROTECTED_ACTION_DENIED: "Your role does not include reviewing this kind of case.",
  KYB_CASE_NOT_FOUND: "No company verification case with this id.",
};

export function errorText(error: unknown): string {
  if (error instanceof ApiError) return ERROR_HELP[String(error.code).toUpperCase()] ?? `${error.code}: ${error.message}`;
  return "Something went wrong. Nothing was saved.";
}

function CaseError({ error }: { error: unknown }) {
  if (error instanceof ApiError && error.status === 403 && !error.isStepUpRequired && !error.isApprovalRequired) {
    return <EmptyState title="Withheld for your role">Your role does not include reviewing this case. Nothing was changed.</EmptyState>;
  }
  if (error instanceof ApiError && error.status !== 0) {
    return (
      <Banner tone="danger" title="Not done" live="assertive">
        {errorText(error)}
      </Banner>
    );
  }
  return <ApiErrorView error={error} />;
}

function RulingForm({ kind, caseId, onDone }: { kind: CaseKind; caseId: string; onDone: () => void }) {
  const outcomes = kind === "SCREENING" ? SCREENING_OUTCOMES : KYB_OUTCOMES;
  const [outcome, setOutcome] = useState<Outcome>("REJECTED");
  const [rationale, setRationale] = useState("");
  const [mandate, setMandate] = useState(false);
  const rule = useCellMutation(
    (api) =>
      kind === "SCREENING"
        ? api.caseReviews.screeningRuling(caseId, { outcome: outcome as "VERIFIED" | "REJECTED" | "REVIEW_REQUIRED", rationale: rationale.trim(), confirm_mandate: mandate && mandateAllowed(kind, outcome) })
        : api.caseReviews.kybRuling(caseId, { outcome: outcome as "VERIFIED" | "REJECTED" | "NEEDS_REVIEW", rationale: rationale.trim() }),
    { invalidate: [["operator", "case-reviews"]], success: "Ruling recorded", onSuccess: () => { setRationale(""); setMandate(false); onDone(); } },
  );
  return (
    <form
      aria-label="Ruling"
      className="space-y-3"
      onSubmit={(e) => {
        e.preventDefault();
        rule.mutate(undefined);
      }}
    >
      <Select
        label="Your ruling"
        value={outcome}
        onChange={(v) => setOutcome((outcomes as readonly string[]).includes(v) ? (v as Outcome) : "REJECTED")}
        options={outcomes.map((o) => ({ id: o, label: OUTCOME_LABEL[o], description: OUTCOME_HELP[o] }))}
      />
      <TextField label="Reason" description="At least 10 characters. It goes on the record." value={rationale} onChange={setRationale} isRequired multiline rows={3} />
      {mandateAllowed(kind, outcome) ? (
        <label className="flex items-start gap-2 text-sm">
          <input type="checkbox" checked={mandate} onChange={(e) => setMandate(e.target.checked)} className="mt-1" />
          <span>I read the delegated mandate and confirm the person may act for the company (only if this case rests on one).</span>
        </label>
      ) : null}
      {rule.error ? <CaseError error={rule.error} /> : null}
      <Button type="submit" pending={rule.isPending} isDisabled={!rulingReady(rationale)}>
        Record ruling
      </Button>
    </form>
  );
}

function ReplayView({ replay }: { replay: ScreeningReplay }) {
  if (!replay.replayable) {
    return (
      <Banner tone="info" title={`Version ${replay.version} cannot be decided again`}>
        {replay.reason === "REVIEWER_RULING" ? "It was a person's ruling, and a ruling is not replayed." : replay.reason === "LIST_VERSION_NOT_KEPT" ? "A list version it used was not kept." : String(replay.reason ?? "")}
      </Banner>
    );
  }
  return (
    <Banner tone={replay.matches ? "ok" : "warn"} title={replay.matches ? `Version ${replay.version} decides the same again` : `Version ${replay.version} does not decide the same again`}>
      {replay.matches ? "Decided again from exactly what it used, it gives the recorded answer." : "Decided again from what it used, it does not match the recorded answer. Look before relying on it."}
    </Banner>
  );
}

function ScreeningCase({ caseId }: { caseId: string }) {
  const detail = useCellQuery(["operator", "case-reviews", "screening", caseId], (api) => api.caseReviews.screening(caseId));
  const [replayVersion, setReplayVersion] = useState<number | null>(null);
  const replay = useCellQuery(["operator", "case-reviews", "replay", caseId, replayVersion], (api) => api.caseReviews.replay(caseId, replayVersion ?? 0), { enabled: replayVersion !== null });
  const alertCols: Column<ScreeningCaseDetail["alerts"][number]>[] = [
    { key: "list", header: "List", pinned: true, render: (a) => a.list_id },
    { key: "score", header: "Match", render: (a) => (typeof a.score === "number" ? a.score.toFixed(2) : "-") },
    { key: "version", header: "List version", render: (a) => <span className="font-mono text-xs">{a.list_version ?? "-"}</span> },
    { key: "reasons", header: "Why it matched", render: (a) => (a.reasons?.length ? a.reasons.join(", ") : "-") },
  ];
  if (detail.isError) return <CaseError error={detail.error} />;
  return (
    <QueryView query={detail}>
      {(d: ScreeningCaseDetail) => (
        <div className="space-y-6">
          <Banner tone="info" title={`Status: ${d.status.replaceAll("_", " ").toLowerCase()}`}>
            A match is a reason to look, not a finding. Only your ruling closes the case.
            {d.reasons.length ? ` Reasons: ${d.reasons.join(", ")}.` : ""}
          </Banner>
          <Card title="Alerts" description="The alerts of the current decision, with the listed names. Restricted: do not copy them elsewhere.">
            <DataTable<ScreeningCaseDetail["alerts"][number]> caption="Screening alerts" rows={d.alerts} rowKey={(a) => `${a.list_id}-${a.list_version ?? ""}-${a.score ?? ""}-${(a.reasons ?? []).join("|")}`} columns={alertCols} empty="No alerts." />
          </Card>
          <Card title="Versions" description="Every decision on this case. Decide a past one again from exactly what it used.">
            {d.versions.length ? (
              <ul className="space-y-2 text-sm">
                {d.versions.map((v) => (
                  <li key={v.version} className="flex items-center gap-3">
                    <span>
                      Version {v.version}: {v.status.replaceAll("_", " ").toLowerCase()} {v.decided_by ? <Badge tone="info">person</Badge> : <Badge tone="neutral">automatic</Badge>}
                    </span>
                    <Button variant="secondary" size="sm" onPress={() => setReplayVersion(v.version)}>
                      {`Decide version ${v.version} again`}
                    </Button>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-sm text-ink-muted">No decisions yet.</p>
            )}
            {replayVersion !== null && replay.data ? <div className="mt-3"><ReplayView replay={replay.data} /></div> : null}
            {replay.error ? <CaseError error={replay.error} /> : null}
          </Card>
          <Card title="Your ruling">
            <RulingForm kind="SCREENING" caseId={caseId} onDone={() => void detail.refetch()} />
          </Card>
        </div>
      )}
    </QueryView>
  );
}

export function CaseReviewsPage() {
  const [kind, setKind] = useState<CaseKind>("SCREENING");
  const [draft, setDraft] = useState("");
  const [open, setOpen] = useState<{ kind: CaseKind; id: string } | null>(null);
  return (
    <>
      <PageHeader
        title="Case reviews"
        description="A person rules on company screening and company verification cases. The system never decides one: a match, an upload or a payment never makes a company verified."
        breadcrumbs={[{ label: "Case reviews" }]}
      />
      <div className="space-y-6">
        <form
          aria-label="Open a case"
          className="flex flex-wrap items-end gap-3"
          onSubmit={(e) => {
            e.preventDefault();
            if (isCaseId(draft)) setOpen({ kind, id: draft.trim().toLowerCase() });
          }}
        >
          <Select label="Kind of case" value={kind} onChange={(v) => setKind(v === "KYB" ? "KYB" : "SCREENING")} options={(Object.keys(KIND_LABEL) as CaseKind[]).map((k) => ({ id: k, label: KIND_LABEL[k] }))} />
          <TextField label="Case id" description="The id of the case you were asked to review" value={draft} onChange={setDraft} isRequired />
          <Button type="submit" isDisabled={!isCaseId(draft)}>
            Open case
          </Button>
        </form>
        {draft && !isCaseId(draft) ? <p className="text-sm text-ink-muted">A case id looks like 8-4-4-4-12 letters and digits.</p> : null}
        {open?.kind === "SCREENING" ? <ScreeningCase caseId={open.id} /> : null}
        {open?.kind === "KYB" ? (
          <div className="space-y-4">
            <Banner tone="info" title="Company verification case">
              Read the case and its documents where you were given them, then record your ruling here. A document proves nothing by itself: you decide what it is worth.
            </Banner>
            <Card title="Your ruling">
              <RulingForm kind="KYB" caseId={open.id} onDone={() => undefined} />
            </Card>
          </div>
        ) : null}
        {!open ? <EmptyState title="No case open">Choose the kind of case, enter its id and open it.</EmptyState> : null}
      </div>
    </>
  );
}
