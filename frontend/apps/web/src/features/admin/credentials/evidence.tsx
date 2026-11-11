"use client";

import type { DocumentValidation, UploadManifest } from "@zoiko/api-client";
import { Banner, Button, Card, DataTable, EmptyState, StatusBadge, type Column } from "@zoiko/ui";
import { FileUp, ShieldCheck } from "lucide-react";
import { useRef, useState } from "react";

import { ApiErrorView, newIdempotencyKey, useCellMutation, useCellQuery } from "@/lib/cell";

import { AdminQueryView } from "../identity-providers/shared";

export const UPLOADS_KEY = ["admin", "credentials", "evidence-uploads"];
const ALLOWED = ["application/pdf", "image/png", "image/jpeg"];

async function sha256Hex(file: Blob): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", await file.arrayBuffer());
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

const kb = (n: number) => (n >= 1024 * 1024 ? `${(n / 1024 / 1024).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`);

/** What the scan said, in words a person can act on. A file is only ever kept after a CLEAN scan. */
function scanWords(u: UploadManifest): string {
  if (u.state === "REJECTED") return `Rejected: ${(u.reject_reason ?? "").toLowerCase().replaceAll("_", " ")}`;
  if (u.state === "AWAITING_CONTENT") return "Waiting for the file";
  if (u.state === "DELETED") return "Deleted; the file is gone";
  if (u.state === "EXPIRED") return "Retention ended; the file is gone";
  return u.scan.state === "CLEAN" ? "Scanned clean" : u.scan.state.toLowerCase().replaceAll("_", " ");
}

/** Evidence documents (ZID-INT-002 section 05). The cell scans every upload and keeps the bytes only if the scan is clean; this
 * page never shows a document, only its state. Retention is set by the organization's privacy officer. */
export function EvidenceSection() {
  const list = useCellQuery(UPLOADS_KEY, (api) => api.uploads.list());
  const input = useRef<HTMLInputElement>(null);
  const [checked, setChecked] = useState<Record<string, DocumentValidation>>({});

  const upload = useCellMutation(
    async (api, file: File) => {
      if (!ALLOWED.includes(file.type)) throw new Error("Only PDF, PNG and JPEG files can be uploaded.");
      const made = await api.uploads.announce({ declared_type: file.type, size_bytes: file.size, sha256: await sha256Hex(file) }, newIdempotencyKey());
      return api.uploads.send(made.upload_url, made.upload_token, file);
    },
    { invalidate: [UPLOADS_KEY], success: (m) => `Uploaded and scanned clean (${m.evidence_ref})` },
  );
  const remove = useCellMutation((api, ref: string) => api.uploads.remove(ref), { invalidate: [UPLOADS_KEY], success: "The file was deleted" });
  const validate = useCellMutation(
    (api, u: UploadManifest) => api.uploads.validate({ evidence_ref: u.evidence_ref, declared_type: u.declared_type, expected_sha256: u.sha256, checks: ["integrity", "issuer", "signature", "seal"] }),
    { onSuccess: (r, u) => setChecked((c) => ({ ...c, [u.evidence_ref]: r })) },
  );

  const columns: Column<UploadManifest>[] = [
    { key: "ref", header: "Evidence", pinned: true, render: (u) => <span className="font-mono text-xs">{u.evidence_ref}</span> },
    { key: "type", header: "Type", render: (u) => `${u.declared_type} · ${kb(u.size_bytes)}` },
    { key: "state", header: "State", render: (u) => <StatusBadge state={u.state === "READY" ? "ACTIVE" : u.state === "REJECTED" ? "REJECTED" : "PENDING"} size="xs" /> },
    { key: "scan", header: "Scan", render: (u) => scanWords(u) },
    { key: "retained", header: "Kept until", render: (u) => (u.state === "READY" ? new Date(u.expires_at).toLocaleDateString() : "—") },
    {
      key: "actions",
      header: "",
      render: (u) =>
        u.state === "READY" ? (
          <div className="flex gap-2">
            <Button size="sm" variant="secondary" onClick={() => validate.mutate(u)} isDisabled={validate.isPending}>
              Check
            </Button>
            <Button size="sm" variant="secondary" onClick={() => remove.mutate(u.evidence_ref)} isDisabled={remove.isPending}>
              Delete
            </Button>
          </div>
        ) : null,
    },
  ];

  return (
    <div className="space-y-6">
      <Banner tone="info" icon={ShieldCheck}>
        Every file is scanned before it is kept, and the cell keeps it only if the scan is clean. A file that fails the scan is recorded as rejected and nothing is stored. Files are
        erased when you delete them or when the retention period ends.
      </Banner>
      <Card title="Upload a document">
        <div className="flex flex-wrap items-center gap-3">
          <input ref={input} type="file" accept=".pdf,.png,.jpg,.jpeg" aria-label="Choose a PDF, PNG or JPEG file" className="text-sm" />
          <Button
            icon={<FileUp aria-hidden className="size-4" />}
            isDisabled={upload.isPending}
            onClick={() => {
              const file = input.current?.files?.[0];
              if (file) upload.mutate(file);
            }}
          >
            {upload.isPending ? "Uploading and scanning…" : "Upload"}
          </Button>
        </div>
        {upload.isError ? <ApiErrorView error={upload.error} title="The upload was not accepted" /> : null}
      </Card>
      <Card title="Uploaded evidence">
        <AdminQueryView query={list}>
          {(r) =>
            r.items.length ? (
              <DataTable caption="Uploaded evidence" columns={columns} rows={r.items} rowKey={(u) => u.evidence_ref} />
            ) : (
              <EmptyState title="Nothing uploaded yet">Documents you upload appear here with their scan result.</EmptyState>
            )
          }
        </AdminQueryView>
        {remove.isError ? <ApiErrorView error={remove.error} title="The file could not be deleted" /> : null}
      </Card>
      {Object.entries(checked).map(([ref, v]) => (
        <Card key={ref} title={`Document check for ${ref}`}>
          <ul className="space-y-1 text-sm">
            {v.document_results.map((r) => (
              <li key={r.check}>
                <span className="font-medium capitalize">{r.check}</span> · <StatusBadge state={r.status === "VERIFIED" ? "ACTIVE" : "PENDING"} size="xs" /> {r.status.toLowerCase()} ·{" "}
                <span className="text-ink-muted">{r.reasons.join(", ").toLowerCase().replaceAll("_", " ")}</span>
              </li>
            ))}
          </ul>
          <p className="mt-2 text-xs text-ink-muted">{v.limitations.join(" ")}</p>
        </Card>
      ))}
    </div>
  );
}
