"use client";

import { Badge, Banner, Button, Card, Select, TextField } from "@zoiko/ui";
import { useEffect, useState } from "react";

import { ApiErrorView, useCellMutation, useCellQuery } from "@/lib/cell";

import { ATTEST_NOTE, checkWords, claimLine, IDV_KEY, keptLine, OPEN_NOTE, reasonProblem, RECAPTURE_TEXT, REJECT_TEXT } from "./logic";

/** What a reviewer does on a case that people decide: look at the photograph (with a reason, for minutes), ask for a better one, or reject for a defined failure. */
export function ReviewerPanel({ caseId }: { caseId: string }) {
  const key = ["idv-cases", caseId, "review"] as const;
  const q = useCellQuery([...key], (api) => api.idv.reviewView(caseId));
  const refresh = { invalidate: [[...key] as never, IDV_KEY as never] };
  const [reason, setReason] = useState("");
  const [shown, setShown] = useState<{ src: string; reads: number } | null>(null);
  const [recapture, setRecapture] = useState("");
  const [reject, setReject] = useState("");
  const open = useCellMutation(
    async (api, why: string) => {
      const grant = await api.idv.mediaAccess(caseId, why);
      const media = await api.idv.mediaRead(caseId, grant.grant_id);
      return { src: `data:${media.content_type};base64,${media.image_base64}`, reads: media.reads_left };
    },
    { success: "Opened and logged", onSuccess: (r) => setShown(r) },
  );
  const ask = useCellMutation((api, code: string) => api.idv.recapture(caseId, code), { ...refresh, success: "A new photograph has been asked for", onSuccess: () => setShown(null) });
  const decline = useCellMutation((api, code: string) => api.idv.reject(caseId, code), { ...refresh, success: "Rejected", onSuccess: () => setShown(null) });
  const attest = useCellMutation((api, expired: boolean) => api.idv.attestDate(caseId, expired), { ...refresh, success: "Your attestation is recorded" });
  useEffect(() => () => setShown(null), []); // the image never outlives the panel
  const r = q.data;
  if (!r) return q.isError ? <ApiErrorView error={q.error} /> : null;
  const problem = reason ? reasonProblem(reason) : null;
  return (
    <Card title="The photograph" description={keptLine(r.photo)}>
      <div className="space-y-3 text-sm">
        {r.photo.has_photo && r.photo.checks ? (
          <ul className="flex flex-wrap gap-2">
            {Object.entries(r.photo.checks).map(([name, result]) => {
              const w = checkWords(name, result);
              return (
                <li key={name}>
                  <Badge tone={w.tone}>
                    {w.label}: {w.text}
                  </Badge>
                </li>
              );
            })}
          </ul>
        ) : null}
        <p className="text-muted">A good score here is about the capture, not about whether the document is genuine. You decide that by looking.</p>
        {r.decision === "APPROVED" ? <p>{claimLine(r.claim)}</p> : null}
        {!r.document_date_known && r.state === "OPEN" ? (
          <div className="space-y-2">
            <Banner tone="warn" title="The document's date is not established">{ATTEST_NOTE}</Banner>
            <div className="flex flex-wrap gap-2">
              <Button variant="secondary" pending={attest.isPending} onPress={() => attest.mutate(false)}>The document is in date</Button>
              <Button variant="secondary" pending={attest.isPending} onPress={() => attest.mutate(true)}>The document has expired</Button>
            </div>
            {attest.error ? <ApiErrorView error={attest.error} /> : null}
          </div>
        ) : null}
        {r.photo.has_photo && !r.photo.expired ? (
          shown ? (
            <div className="space-y-2">
              <img src={shown.src} alt="The applicant's document photograph" className="max-h-[28rem] max-w-full rounded border border-border" />
              <p className="text-muted">Shown {3 - shown.reads} of 3 times. {shown.reads === 0 ? "Ask again to see it once more." : ""}</p>
              <Button variant="secondary" onPress={() => setShown(null)}>Close the photograph</Button>
            </div>
          ) : (
            <div className="space-y-2">
              <TextField label="Why do you need to see it?" value={reason} onChange={setReason} multiline rows={2} />
              {problem ? <p className="text-danger">{problem}</p> : null}
              <p className="text-muted">{OPEN_NOTE}</p>
              <Button pending={open.isPending} isDisabled={reasonProblem(reason) !== null} onPress={() => open.mutate(reason.trim())}>Open the photograph</Button>
              {open.error ? <ApiErrorView error={open.error} /> : null}
            </div>
          )
        ) : null}
        <div className="grid gap-4 sm:grid-cols-2">
          <div className="space-y-2">
            <Select label="Ask for a new photograph because" value={recapture} onChange={setRecapture} options={[{ id: "", label: "Choose a reason" }, ...r.recapture_codes.map((c) => ({ id: c, label: RECAPTURE_TEXT[c] ?? c }))]} />
            <p className="text-muted">Earlier signatures are cleared, the old photograph can no longer be opened, and the applicant is not charged an attempt.</p>
            <Button variant="secondary" pending={ask.isPending} isDisabled={recapture === ""} onPress={() => ask.mutate(recapture)}>Ask for a new photograph</Button>
            {ask.error ? <ApiErrorView error={ask.error} /> : null}
          </div>
          <div className="space-y-2">
            <Select label="Reject because" value={reject} onChange={setReject} options={[{ id: "", label: "Choose a reason" }, ...r.reject_codes.map((c) => ({ id: c, label: REJECT_TEXT[c] ?? c }))]} />
            <Banner tone="warn" title="Rejecting closes the case">
              Use it only for a defined failure. If the photograph is simply poor, ask for a new one instead.
            </Banner>
            <Button variant="secondary" pending={decline.isPending} isDisabled={reject === ""} onPress={() => decline.mutate(reject)}>Reject the case</Button>
            {decline.error ? <ApiErrorView error={decline.error} /> : null}
          </div>
        </div>
      </div>
    </Card>
  );
}
