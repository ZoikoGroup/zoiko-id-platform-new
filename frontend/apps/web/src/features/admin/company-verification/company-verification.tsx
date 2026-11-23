"use client";

import { ApiError, type KybAppeal, type KybHistoryItem, type KybMandate } from "@zoiko/api-client";
import { AccessUnavailable, Badge, Banner, Button, Card, DataTable, DateTime, EmptyState, LoadingRows, PageHeader, Select, TextField } from "@zoiko/ui";
import { useState } from "react";

import { ApiErrorView, useCellMutation, useCellQuery } from "@/lib/cell";

import {
  ACTION_LABEL,
  appealProblem,
  BINDING,
  exportFileName,
  exportProblem,
  explainReason,
  GRADE,
  KYB_KEY,
  mandateProblem,
  needsSecondSignatory,
  nextSteps,
  problemText,
  ROLES,
  SOURCES,
  type MandateForm,
} from "./logic";
import { VerificationJourney } from "./verification-journey";

function Failed({ error }: { error: unknown }) {
  if (error instanceof ApiError && error.status === 403 && !error.isStepUpRequired && !error.isApprovalRequired) {
    const text = problemText(String(error.code ?? ""));
    if (text !== String(error.code ?? "")) return <Banner tone="danger" title="Not allowed">{text}</Banner>;
    return <EmptyState title="Withheld for your role">Your role does not include this. Nothing was changed.</EmptyState>;
  }
  if (error instanceof ApiError && error.status === 404) return <AccessUnavailable correlationId={error.correlationId} />;
  if (error instanceof ApiError && [409, 422].includes(error.status)) return <Banner tone="danger" title="This could not be done">{problemText(String(error.code ?? ""))}</Banner>;
  return <ApiErrorView error={error} />;
}

function GradeCard() {
  const q = useCellQuery(KYB_KEY, (api) => api.kyb.assertion());
  const assess = useCellMutation<void, unknown>((api) => api.kyb.assess(), { invalidate: [KYB_KEY], success: "Assessed again from what is recorded" });
  const a = q.data;
  const now = a?.now ?? (a ? { grade: a.grade, label: a.label, reasons: a.reasons, lowered_since_assessed: false } : undefined);
  const steps = now ? nextSteps(now.reasons) : [];
  return (
    <Card title="Where the company stands" description="What is proven about the company, worked out again each time you open this page. A grade describes proof: it is not permission.">
      <div className="space-y-3 text-sm">
        {q.isPending && q.fetchStatus !== "idle" ? <LoadingRows rows={3} /> : null}
        {q.isError ? <Failed error={q.error} /> : null}
        {a && now ? (
          <>
            <p>
              <Badge tone={GRADE[now.grade].tone}>
                {now.grade} · {now.label}
              </Badge>{" "}
              {GRADE[now.grade].meaning}
            </p>
            {now.lowered_since_assessed ? (
              <Banner tone="warn" title="Lower than when it was last assessed">
                Time alone has lowered it: a check or a mandate has run out. Assess again once it is renewed.
              </Banner>
            ) : null}
            {now.reasons.length ? (
              <div>
                <p className="font-medium text-ink">Why it is not higher</p>
                <ul className="list-disc space-y-1 pl-5">
                  {now.reasons.map((r) => (
                    <li key={r}>{explainReason(r).text}</li>
                  ))}
                </ul>
                {steps.length ? (
                  <>
                    <p className="mt-2 font-medium text-ink">What can move it up</p>
                    <ul className="list-disc space-y-1 pl-5">
                      {steps.map((s) => (
                        <li key={s}>{s}</li>
                      ))}
                    </ul>
                  </>
                ) : null}
              </div>
            ) : null}
            <div>
              <p className="font-medium text-ink">What you may do now</p>
              {a.permitted_actions.length ? (
                <ul className="list-disc pl-5">
                  {a.permitted_actions.map((p) => (
                    <li key={p}>{ACTION_LABEL[p] ?? p}</li>
                  ))}
                </ul>
              ) : (
                <p className="text-ink-muted">Nothing yet. Acts that bind the company also need a verified person with a mandate that names the act.</p>
              )}
            </div>
            <p className="text-ink-muted">
              {a.assessed_at ? (
                <>
                  Last assessed <DateTime value={a.assessed_at} />.{" "}
                </>
              ) : (
                "Never assessed. "
              )}
              {a.recheck_due ? (
                <>
                  Next check due <DateTime value={a.recheck_due} />, sooner if something changes.
                </>
              ) : null}
            </p>
            <Button variant="secondary" pending={assess.isPending} onPress={() => assess.mutate()}>
              Assess again
            </Button>
            {assess.error ? <Failed error={assess.error} /> : null}
          </>
        ) : null}
      </div>
    </Card>
  );
}

function HistoryCard() {
  const q = useCellQuery([...KYB_KEY, "history"], (api) => api.kyb.history());
  return (
    <Card title="History" description="Each assessment is kept; nothing is overwritten.">
      <DataTable<KybHistoryItem>
        caption="Assessments, newest first"
        rows={q.data?.items ?? []}
        rowKey={(r) => String(r.version)}
        empty="No assessments yet."
        columns={[
          { key: "v", header: "No.", pinned: true, render: (r) => r.version },
          { key: "g", header: "Grade", render: (r) => <Badge tone={GRADE[r.grade].tone}>{r.grade}</Badge> },
          { key: "t", header: "Because of", render: (r) => r.trigger.split(":")[0]?.toLowerCase().replaceAll("_", " ") },
          { key: "w", header: "When", render: (r) => <DateTime value={r.assessed_at} /> },
        ]}
      />
    </Card>
  );
}

function MandateCard({ me }: { me: string | undefined }) {
  const list = useCellQuery([...KYB_KEY, "mandates"], (api) => api.kyb.mandates());
  const inv = [KYB_KEY, [...KYB_KEY, "mandates"], [...KYB_KEY, "history"]];
  const record = useCellMutation((api, f: MandateForm) => api.kyb.recordMandate({ person: f.person.trim(), role: f.role, source: f.source, powers: f.powers, evidence_ref: f.evidence.trim(), valid_from: new Date(f.from).toISOString(), valid_to: f.to ? new Date(f.to).toISOString() : null }), { invalidate: inv, success: "Mandate recorded", onSuccess: () => setForm(empty) });
  const cosign = useCellMutation((api, id: string) => api.kyb.cosign(id), { invalidate: inv, success: "Second signature recorded" });
  const revoke = useCellMutation((api, v: { id: string; reason: string }) => api.kyb.revoke(v.id, v.reason), { invalidate: inv, success: "Mandate revoked" });
  const empty: MandateForm = { person: "", role: "DIRECTOR", source: "BOARD_MINUTE", powers: [], evidence: "", from: "", to: "" };
  const [form, setForm] = useState<MandateForm>(empty);
  const [reasons, setReasons] = useState<Record<string, string>>({});
  const problem = form.person || form.powers.length || form.evidence ? mandateProblem(form, me) : null;
  const toggle = (p: string) => setForm((f) => ({ ...f, powers: f.powers.includes(p) ? f.powers.filter((x) => x !== p) : [...f.powers, p] }));
  return (
    <Card title="Who may act for the company" description="A mandate names a verified person, the acts they may do and the dates. Recording one does not make the person verified: that is their own identity check.">
      <div className="space-y-4 text-sm">
        <DataTable<KybMandate>
          caption="Mandates"
          rows={list.data?.items ?? []}
          rowKey={(r) => r.mandate_id}
          empty="No mandates recorded."
          columns={[
            { key: "p", header: "Person", pinned: true, render: (r) => <code className="font-mono text-xs">{r.person.slice(0, 8)}…</code> },
            { key: "a", header: "Acts", render: (r) => r.powers.map((x) => ACTION_LABEL[x] ?? x).join(", ") },
            { key: "w", header: "Valid", render: (r) => (<><DateTime value={r.valid_from} /> to {r.valid_to ? <DateTime value={r.valid_to} /> : "no end"}</>) },
            { key: "s", header: "Status", render: (r) => (r.revoked ? <Badge tone="warn">Revoked</Badge> : r.second_signatory_required && !r.second_signatory_present ? <Badge tone="warn">Needs a second signatory</Badge> : <Badge tone="ok">In force</Badge>) },
            {
              key: "x",
              header: "",
              render: (r) =>
                r.revoked ? null : (
                  <div className="flex flex-wrap items-center gap-2">
                    {r.second_signatory_required && !r.second_signatory_present ? (
                      <Button variant="secondary" pending={cosign.isPending} isDisabled={r.person === me} onPress={() => cosign.mutate(r.mandate_id)}>
                        Sign as second signatory
                      </Button>
                    ) : null}
                    <TextField label="Reason to revoke" value={reasons[r.mandate_id] ?? ""} onChange={(v) => setReasons((x) => ({ ...x, [r.mandate_id]: v }))} />
                    <Button variant="secondary" pending={revoke.isPending} isDisabled={(reasons[r.mandate_id] ?? "").trim().length < 10} onPress={() => revoke.mutate({ id: r.mandate_id, reason: reasons[r.mandate_id] ?? "" })}>
                      Revoke
                    </Button>
                  </div>
                ),
            },
          ]}
        />
        {cosign.error ? <Failed error={cosign.error} /> : null}
        {revoke.error ? <Failed error={revoke.error} /> : null}
        <div className="space-y-3 rounded-control border border-border p-3">
          <p className="font-medium text-ink">Record a mandate</p>
          <TextField label="Person's account identifier" value={form.person} onChange={(v) => setForm({ ...form, person: v })} />
          <Select label="Role" value={form.role} onChange={(v) => setForm({ ...form, role: v })} options={ROLES.map((r) => ({ id: r, label: r.charAt(0) + r.slice(1).toLowerCase() }))} />
          <Select label="What the mandate rests on" value={form.source} onChange={(v) => setForm({ ...form, source: v })} options={SOURCES.map(([id, label]) => ({ id, label }))} />
          <TextField label="Document or record it rests on" value={form.evidence} onChange={(v) => setForm({ ...form, evidence: v })} />
          <fieldset>
            <legend className="font-medium text-ink">Acts it covers</legend>
            <div className="flex flex-wrap gap-3">
              {BINDING.map((p) => (
                <label key={p} className="flex items-center gap-2">
                  <input type="checkbox" checked={form.powers.includes(p)} onChange={() => toggle(p)} />
                  {ACTION_LABEL[p]}
                </label>
              ))}
            </div>
          </fieldset>
          {needsSecondSignatory(form.powers) ? <p className="text-ink-muted">These acts always need a second signatory, who is neither the person nor you.</p> : null}
          <label className="block" htmlFor="kyb-from">Starts</label>
          <input id="kyb-from" type="datetime-local" value={form.from} onChange={(e) => setForm({ ...form, from: e.target.value })} className="rounded-control border border-border bg-surface p-2 text-ink" />
          <label className="block" htmlFor="kyb-to">Ends (optional)</label>
          <input id="kyb-to" type="datetime-local" value={form.to} onChange={(e) => setForm({ ...form, to: e.target.value })} className="rounded-control border border-border bg-surface p-2 text-ink" />
          {problem ? <p className="text-danger">{problem}</p> : null}
          <Button pending={record.isPending} isDisabled={mandateProblem(form, me) !== null} onPress={() => record.mutate(form)}>
            Record the mandate
          </Button>
          {record.error ? <Failed error={record.error} /> : null}
        </div>
      </div>
    </Card>
  );
}

function AppealCard({ latestVersion }: { latestVersion: number | undefined }) {
  const list = useCellQuery([...KYB_KEY, "appeals"], (api) => api.kyb.appeals());
  const [reason, setReason] = useState("");
  const [decision, setDecision] = useState<Record<string, string>>({});
  const open = useCellMutation((api, v: { version: number; reason: string }) => api.kyb.openAppeal(v.version, v.reason), { invalidate: [[...KYB_KEY, "appeals"]], success: "Appeal opened", onSuccess: () => setReason("") });
  const decide = useCellMutation((api, v: { id: string; outcome: "UPHELD" | "ALLOWED"; reason: string }) => api.kyb.decideAppeal(v.id, v.outcome, v.reason), { invalidate: [KYB_KEY, [...KYB_KEY, "appeals"], [...KYB_KEY, "history"]], success: (r) => r.message });
  const problem = reason ? appealProblem(reason) : null;
  return (
    <Card title="Disagree with a decision" description="Someone who took no part in the decision looks again. An appeal never changes what happened: if it is allowed, the company is assessed again and the earlier record stays.">
      <div className="space-y-3 text-sm">
        <DataTable<KybAppeal>
          caption="Appeals"
          rows={list.data?.items ?? []}
          rowKey={(r) => r.appeal_id}
          empty="No appeals."
          columns={[
            { key: "v", header: "Decision", pinned: true, render: (r) => `No. ${r.decision_version}` },
            { key: "s", header: "State", render: (r) => <Badge tone={r.state === "ALLOWED" ? "ok" : r.state === "UPHELD" ? "neutral" : "info"}>{r.state.toLowerCase()}</Badge> },
            { key: "m", header: "What it means", render: (r) => r.message },
            {
              key: "d",
              header: "",
              render: (r) =>
                r.state !== "OPEN" ? null : (
                  <div className="space-y-2">
                    <TextField label="Your reasons" value={decision[r.appeal_id] ?? ""} onChange={(v) => setDecision((x) => ({ ...x, [r.appeal_id]: v }))} />
                    <div className="flex gap-2">
                      {(["UPHELD", "ALLOWED"] as const).map((o) => (
                        <Button key={o} variant="secondary" pending={decide.isPending} isDisabled={appealProblem(decision[r.appeal_id] ?? "") !== null} onPress={() => decide.mutate({ id: r.appeal_id, outcome: o, reason: decision[r.appeal_id] ?? "" })}>
                          {o === "UPHELD" ? "Decision stands" : "Set the decision aside"}
                        </Button>
                      ))}
                    </div>
                  </div>
                ),
            },
          ]}
        />
        {decide.error ? <Failed error={decide.error} /> : null}
        {latestVersion ? (
          <div className="space-y-2">
            <TextField label={`Why the decision (no. ${latestVersion}) is wrong, or what is new`} multiline rows={3} value={reason} onChange={setReason} />
            {problem ? <p className="text-danger">{problem}</p> : null}
            <Button pending={open.isPending} isDisabled={appealProblem(reason) !== null} onPress={() => open.mutate({ version: latestVersion, reason })}>
              Open an appeal
            </Button>
            {open.error ? <Failed error={open.error} /> : null}
          </div>
        ) : null}
      </div>
    </Card>
  );
}

function ExportCard() {
  const [purpose, setPurpose] = useState("");
  const out = useCellMutation((api, p: string) => api.kyb.exportEvidence(p), { success: "Summary signed" });
  function download() {
    if (!out.data) return;
    const blob = new Blob([JSON.stringify(out.data, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = exportFileName(out.data.summary);
    a.click();
    URL.revokeObjectURL(url);
  }
  return (
    <Card title="Signed summary" description="The grade history for one stated purpose, signed and marked with who asked and why. It holds no names, documents or personal data.">
      <div className="space-y-3 text-sm">
        <TextField label="What is it for?" value={purpose} onChange={setPurpose} />
        {purpose && exportProblem(purpose) ? <p className="text-danger">{exportProblem(purpose)}</p> : null}
        <div className="flex gap-2">
          <Button pending={out.isPending} isDisabled={exportProblem(purpose) !== null} onPress={() => out.mutate(purpose.trim())}>
            Create the summary
          </Button>
          {out.data ? (
            <Button variant="secondary" onPress={download}>
              Download
            </Button>
          ) : null}
        </div>
        {out.error ? <Failed error={out.error} /> : null}
      </div>
    </Card>
  );
}

export function CompanyVerificationPage({ me }: { me?: string }) {
  const history = useCellQuery([...KYB_KEY, "history"], (api) => api.kyb.history());
  const latest = history.data?.items[0]?.version;
  return (
    <>
      <PageHeader
        title="Company verification"
        description="What is proven about your company and what that allows. Company identity, ownership, people and authority are separate checks, and each is shown as it stands."
        breadcrumbs={[{ label: "Company verification" }]}
      />
      <div className="space-y-4">
        <VerificationJourney />
        <GradeCard />
        <HistoryCard />
        <MandateCard me={me} />
        <AppealCard latestVersion={latest} />
        <ExportCard />
      </div>
    </>
  );
}
