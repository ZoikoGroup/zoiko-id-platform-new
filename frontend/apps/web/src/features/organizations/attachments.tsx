"use client";

import { ApiError, type ApprovalAttachmentKind } from "@zoiko/api-client";
import { Badge, Banner, Button, Card, DateTime, Select } from "@zoiko/ui";
import { FileUp } from "lucide-react";
import { useId, useRef, useState } from "react";

import { QueryView, useCellMutation, useCellQuery } from "@/lib/cell";

import {
  ATTACHMENT_ACCEPT,
  ATTACHMENT_KINDS,
  attachmentKey,
  attachmentMessage,
  attachmentProblem,
  attachmentSize,
} from "./logic";

const attemptId = () => (typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID().slice(0, 8) : Date.now().toString(36));

/** WF-013C AT-12: documents behind a request. They are scanned, stored encrypted in the organization's region and visible to the people
 * who decide; access ends with the request. The requester adds them before anyone votes; an approver opens them, and each opening is
 * recorded. The list never carries a document, and nothing here is a link anyone else could use. */
export function AttachmentsCard({ orgId, requestId, reference }: { orgId: string; requestId: string; reference: string | null }) {
  const fileId = useId();
  const input = useRef<HTMLInputElement>(null);
  const key = ["org-approvals", orgId, requestId, "attachments"];
  const [attempt, setAttempt] = useState(attemptId);
  const [kind, setKind] = useState<ApprovalAttachmentKind>("SUPPORTING_DOCUMENT");
  const [file, setFile] = useState<File | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const list = useCellQuery(key, (api) => api.org.attachments(orgId, requestId));
  const upload = useCellMutation((api, f: File) => api.org.attach(orgId, requestId, f, kind, attachmentKey(requestId, f, attempt)), {
    invalidate: [key],
    success: "Document added",
    onSuccess: () => {
      setFile(null);
      setAttempt(attemptId());
      if (input.current) input.current.value = "";
    },
  });
  const serverProblem = upload.error instanceof ApiError ? attachmentMessage(upload.error.code) : upload.error ? attachmentMessage(undefined) : null;
  const shown = problem ?? serverProblem;

  return (
    <Card title="Evidence">
      <p className="text-sm text-ink-muted">
        {reference ? (
          <>
            Reference <span className="font-mono text-xs">{reference}</span>.{" "}
          </>
        ) : (
          "No evidence reference was attached. "
        )}
        Documents are scanned, stored encrypted in your data region, and open only for the people who decide this request, until it ends.
      </p>
      <QueryView query={list}>
        {(d) => (
          <>
            {d.attachments.length === 0 ? (
              <p className="mt-3 text-sm text-ink-muted">No documents were attached.</p>
            ) : (
              <ul className="mt-3 divide-y divide-line rounded-lg border border-line" aria-label="Documents on this request">
                {d.attachments.map((a) => (
                  <li key={a.attachment_id} className="flex flex-wrap items-center gap-3 p-3">
                    <div className="min-w-0 flex-1">
                      <p className="break-words font-medium text-ink">{a.label}</p>
                      <p className="text-xs text-ink-muted">
                        {ATTACHMENT_KINDS.find((k) => k.id === a.kind)?.label ?? "Document"} · {attachmentSize(a.size_bytes)} · added <DateTime value={a.uploaded_at} /> · open until{" "}
                        <DateTime value={a.access_ends_at} />
                      </p>
                    </div>
                    <Badge tone={a.scan_status === "CLEAN" ? "ok" : "neutral"}>{a.scan_status === "CLEAN" ? "Scanned" : "Not scanned"}</Badge>
                    {d.may_open ? (
                      <a
                        className="text-sm font-medium text-accent underline"
                        href={`/api/bff/v1/organizations/${encodeURIComponent(orgId)}/approvals/${encodeURIComponent(requestId)}/attachments/${encodeURIComponent(a.attachment_id)}`}
                        rel="noreferrer noopener"
                        download={a.label}
                      >
                        Open
                      </a>
                    ) : null}
                  </li>
                ))}
              </ul>
            )}
            {d.may_attach ? (
              <form
                className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-[minmax(0,16rem)_minmax(0,1fr)_auto] sm:items-end"
                onSubmit={(e) => {
                  e.preventDefault();
                  if (!file) return;
                  const p = attachmentProblem(file);
                  setProblem(p);
                  if (!p) upload.mutate(file);
                }}
              >
                <Select className="min-w-0" label="What kind of document is it?" value={kind} onChange={(v) => setKind(v as ApprovalAttachmentKind)} options={ATTACHMENT_KINDS.map((k) => ({ id: k.id, label: k.label }))} />
                <div className="flex min-w-0 flex-col gap-1">
                  <label htmlFor={fileId} className="text-sm font-medium text-ink">
                    File
                  </label>
                  <input
                    ref={input}
                    id={fileId}
                    type="file"
                    accept={ATTACHMENT_ACCEPT}
                    aria-describedby={`${fileId}-help`}
                    className="min-h-11 w-full min-w-0 rounded-md border border-line bg-surface px-2 py-2 text-sm text-ink file:mr-3 file:min-h-8 file:rounded-md file:border-0 file:bg-sunken file:px-3 file:py-1 file:text-sm file:font-medium file:text-ink"
                    onChange={(e) => {
                      setFile(e.target.files?.[0] ?? null);
                      setProblem(null);
                      upload.reset();
                      setAttempt(attemptId());
                    }}
                  />
                  <p id={`${fileId}-help`} className="text-xs text-ink-muted">
                    PDF, PNG or JPEG, up to 10 MB, up to {d.limit} documents. You can add documents until the first vote is cast.
                  </p>
                </div>
                <Button type="submit" className="min-h-11" pending={upload.isPending} isDisabled={!file} icon={<FileUp aria-hidden className="size-4" />}>
                  Add document
                </Button>
              </form>
            ) : null}
            <div role="status" aria-live="polite" className="mt-3">
              {shown ? (
                <Banner tone="warn" title="That document was not added">
                  {shown}
                </Banner>
              ) : null}
            </div>
          </>
        )}
      </QueryView>
    </Card>
  );
}
