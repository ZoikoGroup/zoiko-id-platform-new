"use client";

import { ApiError, type OrgClaimProofKind } from "@zoiko/api-client";
import { Badge, Banner, Button, Card, DateTime, Select } from "@zoiko/ui";
import { FileUp } from "lucide-react";
import { useId, useRef, useState } from "react";

import { QueryView, useCellMutation, useCellQuery } from "@/lib/cell";

import { PROOF_ACCEPT, PROOF_KINDS, attemptId, proofKey, proofMessage, proofProblem, sizeLabel } from "./org-claim";

/** WF-13B section 06: supporting documents. They help a reviewer; they never verify the company by themselves, and nothing here
 * ever shows a document back. The browser checks size and type for convenience; the server checks the file's own bytes again. */
export function ProofsCard({ claimId }: { claimId: string }) {
  const fileId = useId();
  const input = useRef<HTMLInputElement>(null);
  const [attempt, setAttempt] = useState(attemptId);
  const [kind, setKind] = useState<OrgClaimProofKind>("CERTIFICATE_OF_INCORPORATION");
  const [file, setFile] = useState<File | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const list = useCellQuery(["org-claims", claimId, "proofs"], (api) => api.orgClaims.proofs(claimId));
  const upload = useCellMutation((api, f: File) => api.orgClaims.uploadProof(claimId, f, kind, proofKey(claimId, f, attempt)), {
    invalidate: [["org-claims", claimId, "proofs"]],
    success: "Document added",
    onSuccess: () => {
      setFile(null);
      setAttempt(attemptId());
      if (input.current) input.current.value = "";
    },
  });
  const serverProblem = upload.error instanceof ApiError ? proofMessage(upload.error.code) : upload.error ? proofMessage(undefined) : null;
  const shown = problem ?? serverProblem;

  return (
    <Card
      title="Supporting documents"
      description="Optional. A reviewer may ask for these. A document is read by a person; it never verifies the company on its own."
    >
      <form
        className="grid gap-4 sm:grid-cols-[16rem_1fr_auto] sm:items-end"
        onSubmit={(e) => {
          e.preventDefault();
          if (!file) return;
          const p = proofProblem(file);
          setProblem(p);
          if (!p) upload.mutate(file);
        }}
      >
        <Select label="What kind of document is it?" value={kind} onChange={(v) => setKind(v as OrgClaimProofKind)} options={PROOF_KINDS.map((k) => ({ id: k.id, label: k.label }))} />
        <div className="flex flex-col gap-1">
          <label htmlFor={fileId} className="text-sm font-medium text-ink">
            File
          </label>
          <input
            ref={input}
            id={fileId}
            type="file"
            accept={PROOF_ACCEPT}
            aria-describedby={`${fileId}-help`}
            className="min-h-11 rounded-md border border-line bg-surface px-2 py-2 text-sm text-ink file:mr-3 file:min-h-8 file:rounded-md file:border-0 file:bg-sunken file:px-3 file:py-1 file:text-sm file:font-medium file:text-ink"
            onChange={(e) => {
              setFile(e.target.files?.[0] ?? null);
              setProblem(null);
              upload.reset();
              setAttempt(attemptId());
            }}
          />
          <p id={`${fileId}-help`} className="text-xs text-ink-muted">
            PDF, PNG or JPEG, up to 10 MB. Files are scanned and stored encrypted in your data region.
          </p>
        </div>
        <Button type="submit" className="min-h-11" pending={upload.isPending} isDisabled={!file} icon={<FileUp aria-hidden className="size-4" />}>
          Add document
        </Button>
      </form>
      <div role="status" aria-live="polite" className="mt-3">
        {shown ? (
          <Banner tone="warn" title="That document was not added">
            {shown}
          </Banner>
        ) : null}
      </div>
      <QueryView query={list}>
        {(d) =>
          d.proofs.length === 0 ? (
            <p className="mt-4 text-sm text-ink-muted">No documents added yet.</p>
          ) : (
            <ul className="mt-4 divide-y divide-line rounded-lg border border-line" aria-label="Documents on this claim">
              {d.proofs.map((p) => (
                <li key={p.proof_id} className="flex flex-wrap items-center gap-3 p-3">
                  <div className="min-w-0 flex-1">
                    <p className="break-words font-medium text-ink">{p.label}</p>
                    <p className="text-xs text-ink-muted">
                      {PROOF_KINDS.find((k) => k.id === p.kind)?.label ?? "Document"} · {sizeLabel(p.size_bytes)} · added <DateTime value={p.uploaded_at} />
                    </p>
                  </div>
                  <Badge tone={p.scan_status === "CLEAN" ? "ok" : "neutral"}>{p.scan_status === "CLEAN" ? "Scanned" : "Not scanned"}</Badge>
                </li>
              ))}
            </ul>
          )
        }
      </QueryView>
    </Card>
  );
}
