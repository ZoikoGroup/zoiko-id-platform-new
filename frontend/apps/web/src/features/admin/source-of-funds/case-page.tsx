"use client";

import type { SofCase, SofDecision, SofQuestion } from "@zoiko/api-client";
import { Badge, Banner, Button, Card, Checkbox, DateTime, FieldGroup, PageHeader, RadioCards } from "@zoiko/ui";
import { useState } from "react";

import { useCellMutation, useCellQuery } from "@/lib/cell";

import { DECISION_LABEL, REASONS, SOF_KEY, answerText, canAppeal, decisionOptions, problemText, reasonOptions, stateText } from "./logic";
import { SofQuery, WriteError } from "./shared";

/** One case: what was asked, what was answered, what is missing, who decided what, and the decision form for a reviewer. */
export function SourceOfFundsCasePage({ caseId }: { caseId: string }) {
  const key = [...SOF_KEY, "case", caseId];
  const kase = useCellQuery(key, (api) => api.sourceOfFunds.case(caseId));
  return (
    <>
      <PageHeader
        title="Source of funds case"
        description="A person decides this case. Nothing on this page is a score."
        breadcrumbs={[{ label: "Source of funds", href: "/admin/source-of-funds" }, { label: caseId.slice(0, 8) }]}
      />
      <SofQuery query={kase}>{(data) => <CaseBody data={data} refetch={() => void kase.refetch()} />}</SofQuery>
    </>
  );
}

function CaseBody({ data, refetch }: { data: SofCase; refetch: () => void }) {
  const state = stateText(data.state, data.valid_until);
  const questionnaire = useCellQuery([...SOF_KEY, "questionnaire", data.country], (api) => api.sourceOfFunds.questionnaire(data.country), { enabled: !data.erased });
  const questions = new Map<string, SofQuestion>((questionnaire.data?.questions ?? []).map((q) => [q.question_id, q]));
  return (
    <div className="space-y-4">
      <Banner tone={state.tone === "danger" ? "danger" : state.tone === "ok" ? "ok" : "info"} title={state.label} live="polite">
        {state.meaning}
        {data.valid_until && data.state === "CLEARED" ? (
          <>
            {" "}
            Valid until <DateTime value={data.valid_until} />.
          </>
        ) : null}
      </Banner>
      <p className="text-sm text-ink-muted">{data.notice}</p>

      {data.erased ? (
        <Banner tone="warn" title="Answers erased">
          The person&apos;s answers and evidence references were erased. The state and the decision record below remain.
        </Banner>
      ) : (
        <>
          <Card title="Answers" description={`Questionnaire ${data.questionnaire.version}, ${data.country}`}>
            {Object.keys(data.answers).length ? (
              <dl className="grid gap-3 text-sm sm:grid-cols-[minmax(0,1fr)_minmax(0,2fr)]">
                {Object.entries(data.answers).map(([id, value]) => (
                  <div key={id} className="contents">
                    <dt className="text-ink-muted">{questions.get(id)?.text ?? id}</dt>
                    <dd className="break-words font-medium text-ink">{answerText(questions.get(id), value)}</dd>
                  </div>
                ))}
              </dl>
            ) : (
              <p className="text-sm text-ink-muted">No answers have been sent.</p>
            )}
          </Card>
          <Card title="Evidence" description="References to uploaded documents. The documents are opened from the evidence page, never copied here.">
            {data.evidence.length ? (
              <ul className="divide-y divide-border text-sm">
                {data.evidence.map((e) => (
                  <li key={`${e.question_id}-${e.type}-${e.upload_id}`} className="flex flex-wrap items-center gap-x-3 gap-y-1 py-2">
                    <span className="font-medium">{e.question_id}</span>
                    <Badge>{e.type.toLowerCase().replaceAll("_", " ")}</Badge>
                    <code className="font-mono text-xs">{e.upload_id.slice(0, 8)}</code>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-sm text-ink-muted">No evidence has been referenced.</p>
            )}
          </Card>
          {data.problems.length ? (
            <Banner tone="warn" title="What is missing">
              <ul className="list-disc ps-5">
                {data.problems.map((p) => (
                  <li key={p}>{problemText(p)}</li>
                ))}
              </ul>
            </Banner>
          ) : null}
        </>
      )}

      <Card title="Decisions" description="Every decision, in order. The record is append-only and holds reason codes only.">
        {data.decisions.length ? (
          <ol className="space-y-2 text-sm">
            {data.decisions.map((d) => (
              <li key={`${d.decided_at}-${d.decision}`} className="flex flex-wrap items-center gap-x-3 gap-y-1">
                <Badge tone={d.decision === "CLEAR" ? "ok" : d.decision === "NOT_CLEAR" ? "danger" : "neutral"}>{DECISION_LABEL[d.decision]}</Badge>
                <span className="text-ink-muted">
                  {stateText(d.from_state, null).label} to {stateText(d.to_state, null).label}
                </span>
                <DateTime value={d.decided_at} />
                {d.reason_codes.length ? <span>{d.reason_codes.map((c) => REASONS[c] ?? c).join("; ")}</span> : null}
              </li>
            ))}
          </ol>
        ) : (
          <p className="text-sm text-ink-muted">Nobody has decided this case yet.</p>
        )}
      </Card>

      {!data.erased && decisionOptions(data.state).length ? <DecisionForm data={data} onDone={refetch} /> : null}
      {canAppeal(data.state) ? <AppealControl caseId={data.case_id} onDone={refetch} /> : null}
    </div>
  );
}

/** A reviewer's decision. The platform refuses a decision by the person who submitted the case and an appeal decided by the first reviewer; this form says so
 * before it is sent, and shows the platform's own refusal if it is sent anyway. */
function DecisionForm({ data, onDone }: { data: SofCase; onDone: () => void }) {
  const options = decisionOptions(data.state);
  const reasons = reasonOptions(data.state);
  const [decision, setDecision] = useState<string>("");
  const [picked, setPicked] = useState<string[]>([]);
  const decide = useCellMutation((api, body: { decision: SofDecision; reason_codes: string[] }) => api.sourceOfFunds.decide(data.case_id, body), {
    invalidate: [SOF_KEY],
    success: "Decision recorded",
    onSuccess: onDone,
  });
  const ready = decision !== "" && picked.length > 0;
  return (
    <Card title={data.state === "APPEALED" ? "Decide the appeal" : "Decide this case"} description="You cannot decide a case you submitted. An appeal is decided by someone other than the first reviewer.">
      <div className="space-y-4">
        <RadioCards label="Decision" options={options.map((o) => ({ id: o, label: DECISION_LABEL[o] }))} value={decision} onChange={setDecision} />
        <FieldGroup legend="Reasons" description="Choose at least one. Only these fixed reasons are kept: no free text is stored.">
          {reasons.map((code) => (
            <Checkbox key={code} isSelected={picked.includes(code)} onChange={(on) => setPicked((p) => (on ? [...p, code] : p.filter((c) => c !== code)))}>
              {REASONS[code]}
            </Checkbox>
          ))}
        </FieldGroup>
        <Button isDisabled={!ready} pending={decide.isPending} onPress={() => decide.mutate({ decision: decision as SofDecision, reason_codes: picked })}>
          Record decision
        </Button>
        {decide.error ? <WriteError error={decide.error} /> : null}
      </div>
    </Card>
  );
}

function AppealControl({ caseId, onDone }: { caseId: string; onDone: () => void }) {
  const appeal = useCellMutation((api) => api.sourceOfFunds.appeal(caseId), { invalidate: [SOF_KEY], success: "Appeal recorded", onSuccess: onDone });
  return (
    <Card title="Appeal" description="Only the person this case is about, or whoever sent it for them, can appeal. A different reviewer then decides.">
      <Button variant="secondary" pending={appeal.isPending} onPress={() => appeal.mutate(undefined)}>
        Appeal this decision
      </Button>
      {appeal.error ? <WriteError error={appeal.error} /> : null}
    </Card>
  );
}
