"use client";

import { ApiError, type AgeCheckResult } from "@zoiko/api-client";
import { Badge, Banner, Button, Card, PageHeader, Select, TextField } from "@zoiko/ui";
import { useState } from "react";

import { ApiErrorView, useCellMutation } from "@/lib/cell";

import { EMPTY, ERROR_TEXT, formProblem, METHOD, reasonWords, RESULT, THRESHOLDS, type AgeForm } from "./logic";

function Failed({ error }: { error: unknown }) {
  if (error instanceof ApiError && [403, 422].includes(error.status)) {
    return <Banner tone="danger" title="This could not be done">{ERROR_TEXT[String(error.code ?? "")] ?? "Your role does not include this, or the request was not accepted."}</Banner>;
  }
  return <ApiErrorView error={error} />;
}

export function AgeCheckPage() {
  const [form, setForm] = useState<AgeForm>(EMPTY);
  const [answer, setAnswer] = useState<AgeCheckResult | null>(null);
  const check = useCellMutation((api, f: AgeForm) => api.verification.ageCheck({ threshold: f.threshold, birth_date: f.birthDate }), { onSuccess: (r) => setAnswer(r) });
  const problem = form.birthDate ? formProblem(form) : null;
  return (
    <>
      <PageHeader
        title="Age check"
        description="Ask whether someone is at least an age. A birth date typed here is the person's own statement and is never verified, so this screen can show how the check answers, but it cannot prove an age."
        breadcrumbs={[{ label: "Age check" }]}
      />
      <div className="space-y-4">
        <Banner tone="info" title="What this can and cannot do">
          A typed birth date can show that someone is under an age, or that the evidence is not enough. It can never show that they are at least the age: that needs a verified birth date, which comes from reading a passport chip in the phone app, or an approved estimate. Nothing is stored and no birth date is returned.
        </Banner>
        <Card title="The question">
          <div className="space-y-3 text-sm">
            <Select label="At least" value={String(form.threshold)} onChange={(v) => setForm({ ...form, threshold: Number(v) })} options={THRESHOLDS.map((t) => ({ id: String(t), label: `${t} years` }))} />
            <TextField label="Birth date (year-month-day), as the person states it" value={form.birthDate} onChange={(v) => setForm({ ...form, birthDate: v })} />
            {problem ? <p className="text-danger">{problem}</p> : null}
            <Button pending={check.isPending} isDisabled={formProblem(form) !== null} onPress={() => check.mutate(form)}>Check</Button>
            {check.error ? <Failed error={check.error} /> : null}
          </div>
        </Card>
        {answer ? (
          <Card title="The answer" description={RESULT[answer.result].meaning}>
            <div className="space-y-2 text-sm">
              <p>
                <Badge tone={RESULT[answer.result].tone}>{RESULT[answer.result].label}</Badge> {answer.threshold} years, from: {METHOD[answer.method]}.
              </p>
              {answer.reason_codes.length ? <ul className="list-disc pl-5">{answer.reason_codes.map((c) => <li key={c}>{reasonWords(c)}</li>)}</ul> : null}
              {answer.fallback_offered ? <p>A documented check should be offered instead.</p> : null}
              <ul className="list-disc pl-5 text-muted">{answer.limitations.map((l) => <li key={l}>{l}</li>)}</ul>
            </div>
          </Card>
        ) : null}
      </div>
    </>
  );
}
