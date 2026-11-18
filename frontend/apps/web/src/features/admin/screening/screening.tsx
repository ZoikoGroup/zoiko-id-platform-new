"use client";

import { ApiError, type ScreeningCaseView, type ScreeningClass, type ScreeningEligibility } from "@zoiko/api-client";
import { Badge, Banner, Button, Card, EmptyState, LoadingRows, PageHeader, Select, TextField } from "@zoiko/ui";
import { useRef, useState } from "react";

import { ApiErrorView, useCellMutation, useCellQuery } from "@/lib/cell";
import { useRuntime } from "@/lib/runtime";

import { CLASS_LABEL, caseProblem, EMPTY_CASE, ERROR_TEXT, FINDING_TONE, ITEM_LABEL, newKey, splitList, SUBJECT_TYPES, type CaseForm } from "./logic";

function Failed({ error }: { error: unknown }) {
  if (error instanceof ApiError && error.status === 403 && !error.isStepUpRequired && !error.isApprovalRequired) {
    return <EmptyState title="Withheld for your role">{ERROR_TEXT[String(error.code ?? "")] ?? "Only an owner or an administrator can request screening."}</EmptyState>;
  }
  if (error instanceof ApiError && [404, 422].includes(error.status)) {
    return <Banner tone="danger" title="This could not be done">{ERROR_TEXT[String(error.code ?? "")] ?? error.message}</Banner>;
  }
  return <ApiErrorView error={error} />;
}

function Eligibility({ form }: { form: CaseForm }) {
  const ready = form.market.trim().length === 2 && form.purpose.trim().length > 0 && form.subjectRef.trim().length > 0;
  const q = useCellQuery<ScreeningEligibility>(
    ["screening", "eligibility", form.market, form.purpose, form.subjectType, form.subjectRef, form.classes.join(",")],
    (api) => api.screening.eligibility({ market: form.market.trim().toUpperCase(), purpose: form.purpose.trim(), subject_type: form.subjectType, subject_ref: form.subjectRef.trim(), classes: form.classes.join(",") }),
    { enabled: ready },
  );
  if (!ready) return <p className="text-sm text-ink-muted">Fill in the market, the purpose and who is being checked to see what is available.</p>;
  if (q.isPending && q.fetchStatus !== "idle") return <LoadingRows rows={2} />;
  if (q.isError) return <Failed error={q.error} />;
  const e = q.data;
  if (!e) return null;
  return (
    <div className="space-y-2 text-sm">
      <p>{e.eligible ? <Badge tone="ok">Available</Badge> : <Badge tone="warn">Not available yet</Badge>}</p>
      {e.blocked.length ? (
        <Banner tone="warn" title="Why not">
          <ul className="list-disc space-y-1 pl-5">{e.blocked.map((b) => <li key={b.code}>{b.text}</li>)}</ul>
        </Banner>
      ) : null}
      <ul className="space-y-1">
        {e.classes.map((c) => (
          <li key={c.class}>
            <strong>{CLASS_LABEL[c.class]}</strong>: {c.available ? `available (${c.tier === "LICENSED" ? "licensed" : "official"} source)` : "not available"}
            {!c.available && c.reasons.length ? <span className="text-ink-muted"> {c.reasons.filter((r) => !e.blocked.some((b) => b.code === r.code)).map((r) => r.text).join(" ")}</span> : null}
          </li>
        ))}
      </ul>
      {e.note ? <p className="text-ink-muted">{e.note}</p> : null}
    </div>
  );
}

function Information({ caseId }: { caseId: string }) {
  const q = useCellQuery(["screening", "information", caseId], (api) => api.screening.information(caseId));
  const [done, setDone] = useState<string[]>([]);
  const send = useCellMutation((api, provided: string[]) => api.screening.replyWithInformation(caseId, provided), { invalidate: [["screening", "information", caseId]], success: "Thank you: we have it", onSuccess: () => setDone([]) });
  const open = q.data?.request;
  if (!open || (open.state !== "REQUESTED" && open.state !== "RECEIVED")) return null;
  const toggle = (id: string) => setDone((x) => (x.includes(id) ? x.filter((i) => i !== id) : [...x, id]));
  return (
    <Card title="We need a little more information" description={open.message}>
      <div className="space-y-3 text-sm">
        {open.state === "RECEIVED" ? <p>Thank you. We have what you sent and will be in touch if we need anything else.</p> : null}
        {open.state === "REQUESTED" ? (
          <fieldset className="space-y-1">
            <legend className="font-medium">Tick what you have now sent us</legend>
            {open.still_needed.map((item) => (
              <label key={item} className="flex items-center gap-2">
                <input type="checkbox" checked={done.includes(item)} onChange={() => toggle(item)} /> {ITEM_LABEL[item] ?? item}
              </label>
            ))}
            <Button pending={send.isPending} isDisabled={done.length === 0} onPress={() => send.mutate(done)}>Confirm</Button>
          </fieldset>
        ) : null}
        {send.error ? <Failed error={send.error} /> : null}
      </div>
    </Card>
  );
}

function Result({ c }: { c: ScreeningCaseView }) {
  const [note, setNote] = useState("");
  const ask = useCellMutation((api, text: string) => api.screening.requestReview(c.request_id, text), { success: "A reviewer will look again", onSuccess: () => setNote("") });
  return (
    <Card title="Result" description={`Checked ${c.checked_at ? new Date(c.checked_at).toLocaleString() : "just now"}. This records what was checked: it is not a clearance, and it says nothing about the person or company beyond that.`}>
      <div className="space-y-3 text-sm">
        <p>
          <Badge tone={FINDING_TONE[c.finding] ?? "neutral"}>{c.finding}</Badge>
        </p>
        <dl className="grid gap-1 sm:grid-cols-2">
          <div className="flex justify-between gap-3 border-b border-border py-1"><dt className="text-ink-muted">Overall</dt><dd>{c.coverage}</dd></div>
          <div className="flex justify-between gap-3 border-b border-border py-1"><dt className="text-ink-muted">Official sanctions lists</dt><dd>{c.official_sanctions}</dd></div>
          <div className="flex justify-between gap-3 border-b border-border py-1"><dt className="text-ink-muted">Politically exposed persons</dt><dd>{c.pep_rca}</dd></div>
          <div className="flex justify-between gap-3 border-b border-border py-1"><dt className="text-ink-muted">Adverse media</dt><dd>{c.adverse_media}</dd></div>
        </dl>
        <p className="text-ink-muted">{c.note}</p>
        <TextField label="Ask for another look (say what to check)" value={note} onChange={setNote} multiline rows={2} />
        <Button variant="secondary" pending={ask.isPending} isDisabled={note.trim().length < 10} onPress={() => ask.mutate(note.trim())}>Ask for another look</Button>
        {ask.error ? <Failed error={ask.error} /> : null}
      </div>
    </Card>
  );
}

export function ScreeningPage() {
  const { session } = useRuntime();
  const tenantId = session?.authenticated ? session.subject.tenantId : undefined;
  const [form, setForm] = useState<CaseForm>({ ...EMPTY_CASE, subjectRef: tenantId ?? "" });
  const key = useRef<string>(newKey());
  const problem = form.name || form.subjectRef ? caseProblem(form, tenantId) : null;
  const open = useCellMutation(
    (api, f: CaseForm) =>
      api.screening.openCase(
        { market: f.market.trim().toUpperCase(), purpose: f.purpose.trim(), subject_type: f.subjectType, subject_ref: f.subjectRef.trim(), name: f.name.trim(), birth: f.birth.trim() || null, countries: splitList(f.countries), classes: f.classes },
        key.current,
      ),
    { success: "Screening done", onSuccess: () => (key.current = newKey()) },
  );
  const set = (patch: Partial<CaseForm>) => setForm((f) => ({ ...f, ...patch }));
  const toggle = (c: ScreeningClass) => set({ classes: form.classes.includes(c) ? form.classes.filter((x) => x !== c) : [...form.classes, c] });
  return (
    <>
      <PageHeader
        title="Screening"
        description="Check a company, an owner or a representative against the sanctions lists your market allows. What is available depends on a signed policy for your market. Where only the official lists are used, this is official sanctions screening: it is not an AML clearance."
        breadcrumbs={[{ label: "Screening" }]}
      />
      <div className="space-y-4">
        <Card title="What would be checked" description="Each check names the sources behind it. Nothing is hidden by a greyed-out button: if something is not available, the reason is written here.">
          <div className="space-y-3 text-sm">
            <TextField label="Market (two-letter country code)" value={form.market} onChange={(v) => set({ market: v })} />
            <TextField label="Purpose" value={form.purpose} onChange={(v) => set({ purpose: v })} />
            <Select label="Who is being checked" value={form.subjectType} onChange={(v) => set({ subjectType: v as CaseForm["subjectType"], subjectRef: v === "BUSINESS" ? (tenantId ?? "") : "" })} options={SUBJECT_TYPES.map(([id, label]) => ({ id, label }))} />
            {form.subjectType !== "BUSINESS" ? <TextField label="The person's account identifier" value={form.subjectRef} onChange={(v) => set({ subjectRef: v })} /> : null}
            <fieldset>
              <legend className="font-medium text-ink">Checks</legend>
              <div className="flex flex-wrap gap-3">
                {(Object.keys(CLASS_LABEL) as ScreeningClass[]).map((c) => (
                  <label key={c} className="flex items-center gap-2">
                    <input type="checkbox" checked={form.classes.includes(c)} onChange={() => toggle(c)} />
                    {CLASS_LABEL[c]}
                  </label>
                ))}
              </div>
            </fieldset>
            <Eligibility form={form} />
          </div>
        </Card>
        <Card title="Run a screening" description="Use the name exactly as it appears on the identity record. A birth year or country helps tell people with the same name apart; leaving them out never counts against the person.">
          <div className="space-y-3 text-sm">
            <TextField label="Name" value={form.name} onChange={(v) => set({ name: v })} />
            <TextField label="Birth year or date (optional)" value={form.birth} onChange={(v) => set({ birth: v })} />
            <TextField label="Countries, separated by commas (optional)" value={form.countries} onChange={(v) => set({ countries: v })} />
            {problem ? <p className="text-danger">{problem}</p> : null}
            <Button pending={open.isPending} isDisabled={caseProblem(form, tenantId) !== null} onPress={() => open.mutate(form)}>Run the screening</Button>
            {open.error ? <Failed error={open.error} /> : null}
          </div>
        </Card>
        {open.data ? <Result c={open.data} /> : null}
        {open.data ? <Information caseId={open.data.request_id} /> : null}
      </div>
    </>
  );
}
