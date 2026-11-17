"use client";

import { ApiError, type IdvCase } from "@zoiko/api-client";
import { Badge, Banner, Button, Card, EmptyState, LoadingRows, PageHeader, TextField } from "@zoiko/ui";
import { useState } from "react";

import { ApiErrorView, useCellMutation, useCellQuery } from "@/lib/cell";

import { applicantText, fileToBase64, imageProblem, MY_IDV_KEY, nextStep, outcomeOf, STEP_LABEL, type Step } from "./logic";

function Failed({ error }: { error: unknown }) {
  if (error instanceof ApiError && [403, 404, 409, 422, 429, 503].includes(error.status)) {
    return <Banner tone="danger" title="This could not be done">{applicantText(String(error.code ?? ""))}</Banner>;
  }
  return <ApiErrorView error={error} />;
}

function Progress({ at }: { at: Step }) {
  const order: Step[] = ["START", "NOTICE", "DOCUMENT", "CHIP", "SELFIE", "STATUS"];
  return (
    <ol className="flex flex-wrap gap-2 text-sm" aria-label="Steps">
      {order.map((s) => (
        <li key={s}>
          <Badge tone={s === at ? "info" : "neutral"}>{STEP_LABEL[s]}</Badge>
        </li>
      ))}
    </ol>
  );
}

function CaseCard({ c }: { c: IdvCase }) {
  const [file, setFile] = useState<File | null>(null);
  const notice = c.notice_version ?? "";
  const refresh = { invalidate: [MY_IDV_KEY] };
  const consent = useCellMutation((api, v: { consented: boolean }) => api.idv.consent(c.case_id, notice, v.consented), { ...refresh, success: "Saved" });
  const withdraw = useCellMutation((api) => api.idv.withdraw(c.case_id), { ...refresh, success: "Consent withdrawn" });
  const abandon = useCellMutation((api) => api.idv.abandon(c.case_id), { ...refresh, success: "Check ended" });
  const send = useCellMutation(
    async (api, f: File) => {
      const { nonce } = await api.idv.nonce(c.case_id);
      return api.idv.documentImage(c.case_id, { nonce, image_base64: await fileToBase64(f), captured_at: new Date().toISOString() });
    },
    { ...refresh, success: "Photograph received", onSuccess: () => setFile(null) },
  );
  const step = nextStep(c);
  const outcome = outcomeOf(c.decision);
  const problem = imageProblem(file);
  return (
    <Card title={`${c.market} identity check`} description="Each step is separate. Missing evidence is never read as a pass.">
      <div className="space-y-4 text-sm">
        <Progress at={step} />
        <Banner tone={outcome.tone === "danger" ? "danger" : outcome.tone === "warn" ? "warn" : "info"} title={c.decision.toLowerCase().replace(/_/g, " ")}>
          {outcome.text}
        </Banner>
        {c.state === "OPEN" ? (
          <>
            {step === "DOCUMENT" ? (
              <div className="space-y-2">
                <p>Take a clear photograph of the page with your photo and the two lines of characters at the bottom. Flat, in good light, with nothing covering it.</p>
                <input type="file" accept="image/jpeg,image/png" onChange={(e) => setFile(e.target.files?.[0] ?? null)} aria-label="Document photograph" />
                {file && problem ? <p className="text-danger">{problem}</p> : null}
                <Button pending={send.isPending} isDisabled={problem !== null} onPress={() => file && send.mutate(file)}>
                  Send the photograph
                </Button>
                {send.error ? <Failed error={send.error} /> : null}
              </div>
            ) : null}
            {step === "CHIP" ? <p>Open the Zoiko app on your phone and hold it against your passport to read the chip. A browser cannot read a chip.</p> : null}
            {step === "SELFIE" ? <p>The selfie is taken in the phone app, after the chip, so the face is compared with the portrait the chip holds.</p> : null}
            <div className="space-y-2">
              <p className="font-medium">Face checks and your consent</p>
              {notice === "" ? <p>No notice has been published for this country yet, so consent cannot be given and face checks stay off.</p> : <p>You will be shown notice {notice} before you agree.</p>}
              <div className="flex flex-wrap gap-2">
                <Button variant="secondary" pending={consent.isPending} isDisabled={notice.trim() === ""} onPress={() => consent.mutate({ consented: true })}>
                  I have read the notice and agree
                </Button>
                <Button variant="secondary" pending={withdraw.isPending} onPress={() => withdraw.mutate(undefined)}>
                  Withdraw my consent
                </Button>
                <Button variant="secondary" pending={abandon.isPending} onPress={() => abandon.mutate(undefined)}>
                  Stop this check
                </Button>
              </div>
              {consent.error ? <Failed error={consent.error} /> : null}
            </div>
          </>
        ) : null}
        {c.reasons.length ? (
          <ul className="list-disc pl-5">
            {[...new Set(c.reasons.map(applicantText))].map((t) => (
              <li key={t}>{t}</li>
            ))}
          </ul>
        ) : null}
      </div>
    </Card>
  );
}

export function IdentityProofingPage() {
  const q = useCellQuery(MY_IDV_KEY, (api) => api.idv.cases());
  const [market, setMarket] = useState("");
  const open = useCellMutation((api, m: string) => api.idv.open(m.trim().toUpperCase()), { invalidate: [MY_IDV_KEY], success: "Check started", onSuccess: () => setMarket("") });
  return (
    <div className="space-y-6">
      <PageHeader title="Identity check" description="Verify your identity with your passport. You will photograph the document here, then read the chip and take a selfie in the phone app." />
      {q.isPending && q.fetchStatus !== "idle" ? <LoadingRows rows={3} /> : null}
      {q.isError ? <Failed error={q.error} /> : null}
      <Card title="Start a check" description="Use the two-letter code of the country that issued your document.">
        <div className="flex flex-wrap items-end gap-3">
          <TextField label="Country code" value={market} onChange={setMarket} />
          <Button pending={open.isPending} isDisabled={!/^[A-Za-z]{2}$/.test(market.trim())} onPress={() => open.mutate(market)}>
            Start
          </Button>
        </div>
        {open.error ? <Failed error={open.error} /> : null}
      </Card>
      {q.data && q.data.items.length === 0 ? <EmptyState title="No checks yet">Start one above.</EmptyState> : null}
      {(q.data?.items ?? []).map((c) => (
        <CaseCard key={c.case_id} c={c} />
      ))}
    </div>
  );
}
