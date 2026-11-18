"use client";

import { ApiError, type IdvCase } from "@zoiko/api-client";
import { Badge, Banner, Button, Card, EmptyState, LoadingRows, PageHeader, TextField } from "@zoiko/ui";
import { useState } from "react";

import { ApiErrorView, useCellMutation, useCellQuery } from "@/lib/cell";

import { DocumentCapture } from "./document-capture";
import { applicantText, fileToBase64, imageProblem, MY_IDV_KEY, chipLink, handoff, nextStep, onPhone, outcomeOf, STEP_LABEL, stepsFor, WEB_ROUTE, type Step } from "./logic";

function Failed({ error }: { error: unknown }) {
  if (error instanceof ApiError && [403, 404, 409, 422, 429, 503].includes(error.status)) {
    return <Banner tone="danger" title="This could not be done">{applicantText(String(error.code ?? ""))}</Banner>;
  }
  return <ApiErrorView error={error} />;
}

function Progress({ at, route }: { at: Step; route: string }) {
  const order = stepsFor(route);
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

/** The chip step: a browser cannot read a chip, so the phone app does it for this same case. This page keeps watching the case and moves on by itself when the app is done. */
function PhoneHandoff({ c }: { c: IdvCase }) {
  const link = chipLink(c);
  const phone = typeof navigator !== "undefined" && onPhone(navigator.userAgent);
  const [note, setNote] = useState("");
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(link);
      setNote("Link copied. Send it to your phone and open it there.");
    } catch {
      setNote("Copy this link by hand: " + link);
    }
  };
  const share = async () => {
    try {
      await navigator.share({ title: "Zoiko identity check", url: link });
    } catch {
      /* the person closed the share sheet */
    }
  };
  return (
    <div className="space-y-2" aria-live="polite">
      <p className="font-medium">Next: read your passport chip in the phone app</p>
      <p>A browser cannot read a chip, so your phone does it for this same check. Sign in to the Zoiko app with the same account, open this check, then hold the top of the phone against the passport photo page.</p>
      {phone ? (
        <a className="inline-block rounded border px-3 py-2" href={link}>
          Open the Zoiko app
        </a>
      ) : (
        <div className="flex flex-wrap gap-2">
          <Button variant="secondary" onPress={copy}>
            Copy the link for my phone
          </Button>
          {typeof navigator !== "undefined" && "share" in navigator ? (
            <Button variant="secondary" onPress={share}>
              Share to my phone
            </Button>
          ) : null}
        </div>
      )}
      {note ? <p role="status">{note}</p> : null}
      <p className="text-ink-muted">This page updates by itself when the app has finished. No app, or a phone without NFC? Stop this check and start one with "Verify in this browser only"; people will review your photograph.</p>
      {handoff(c) === "WAITING_FOR_APP" ? <p className="text-ink-muted">Waiting for the app…</p> : null}
    </div>
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
        <Progress at={step} route={c.route} />
        <Banner tone={outcome.tone === "danger" ? "danger" : outcome.tone === "warn" ? "warn" : "info"} title={c.decision.toLowerCase().replace(/_/g, " ")}>
          {outcome.text}
        </Banner>
        {c.state === "OPEN" ? (
          <>
            {step === "DOCUMENT" ? (
              <div className="space-y-2">
                <p>Take a clear photograph of the page with your photo and the two lines of characters at the bottom. Flat, in good light, with nothing covering it.</p>
                <DocumentCapture onCaptured={setFile} disabled={send.isPending} />
                <p className="text-ink-muted">Or choose a photograph from your device:</p>
                <input type="file" accept="image/jpeg,image/png" onChange={(e) => setFile(e.target.files?.[0] ?? null)} aria-label="Document photograph" />
                {file && !problem ? <p role="status">Ready to send: {file.name} ({Math.round(file.size / 1024)} KB)</p> : null}
                {file && problem ? <p className="text-danger">{problem}</p> : null}
                <Button pending={send.isPending} isDisabled={problem !== null} onPress={() => file && send.mutate(file)}>
                  Send the photograph
                </Button>
                {send.error ? <Failed error={send.error} /> : null}
              </div>
            ) : null}
            {step === "STATUS" && c.route !== "CHIP" ? <p>Your photograph is with our reviewers. Two people who work independently of each other have to look at it before this check can be complete. This way of verifying never says your passport chip was read.</p> : null}
            {step === "CHIP" ? <PhoneHandoff c={c} /> : null}
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
  const q = useCellQuery(MY_IDV_KEY, (api) => api.idv.cases(), { refetchInterval: 5000 });
  const [market, setMarket] = useState("");
  const open = useCellMutation((api, v: { market: string; route: string }) => api.idv.open(v.market.trim().toUpperCase(), v.route), { invalidate: [MY_IDV_KEY], success: "Check started", onSuccess: () => setMarket("") });
  return (
    <div className="space-y-6">
      <PageHeader title="Identity check" description="Verify your identity with your passport. Photograph the document here. The strongest check also reads the chip in the phone app; if you have no app, you can verify with this browser alone and people will review your photograph." />
      {q.isPending && q.fetchStatus !== "idle" ? <LoadingRows rows={3} /> : null}
      {q.isError ? <Failed error={q.error} /> : null}
      <Card title="Start a check" description="Use the two-letter code of the country that issued your document.">
        <div className="flex flex-wrap items-end gap-3">
          <TextField label="Country code" value={market} onChange={setMarket} />
          <Button pending={open.isPending} isDisabled={!/^[A-Za-z]{2}$/.test(market.trim())} onPress={() => open.mutate({ market, route: "CHIP" })}>
            Start with the phone app
          </Button>
          <Button variant="secondary" pending={open.isPending} isDisabled={!/^[A-Za-z]{2}$/.test(market.trim())} onPress={() => open.mutate({ market, route: WEB_ROUTE })}>
            Verify in this browser only
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
