"use client";

import type { ClaimJudgement } from "@zoiko/api-client";
import { Button, Card, DataTable, Dialog, EmptyState, JsonView, PageHeader, Select, TextField, type Column } from "@zoiko/ui";
import { Megaphone } from "lucide-react";
import { useState } from "react";

import { QueryView, useCellMutation, useCellQuery } from "@/lib/op";

import { Facts, Hash, Mono, REG, Reasons, RegError, Verdict } from "./common";

const AUDIENCES = ["PUBLIC_WEBSITE", "PRODUCT_LABEL", "SALES", "SUPPORT", "API"];
export const EXPORT_PURPOSES = ["REGULATOR_REQUEST", "AUDIT", "LEGAL_CLAIM", "INTERNAL_REVIEW"];

/** A minimal, purpose-bound proof export (section 09.2 evidence bundle). The export and any refusal are audited. */
export function ExportDialog({ bundleId, onClose }: { bundleId: string | null; onClose: () => void }) {
  const [purpose, setPurpose] = useState("REGULATOR_REQUEST");
  const [basis, setBasis] = useState("");
  const exp = useCellMutation((api, id: string) => api.regulatory.evidenceBundle(id, purpose, basis.trim()), { success: "Bundle exported (audited)" });
  const download = () => {
    if (!exp.data || !bundleId) return;
    const blob = new Blob([JSON.stringify(exp.data, null, 2)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `${bundleId.replace(":", "-")}.json`;
    a.click();
    URL.revokeObjectURL(a.href);
  };
  const close = () => {
    exp.reset();
    setBasis("");
    onClose();
  };
  return (
    <Dialog
      isOpen={bundleId !== null}
      onOpenChange={(o) => !o && close()}
      title={`Export proof ${bundleId ?? ""}`}
      description="Only what proves the point leaves the platform: ids, codes, hashes and references, never personal data. State why it is needed."
      size="lg"
      footer={
        <>
          <Button variant="secondary" onPress={close}>
            Close
          </Button>
          {exp.data ? (
            <Button onPress={download}>Download</Button>
          ) : (
            <Button onPress={() => bundleId && exp.mutate(bundleId)} pending={exp.isPending} isDisabled={!basis.trim()}>
              Export
            </Button>
          )}
        </>
      }
    >
      <div className="space-y-3">
        <Select label="Purpose" value={purpose} onChange={setPurpose} options={EXPORT_PURPOSES.map((p) => ({ id: p, label: p }))} />
        <TextField label="Legal basis reference" description="e.g. the regulator's request number" value={basis} onChange={setBasis} isRequired />
        {exp.error ? <RegError error={exp.error} /> : null}
        {exp.data ? (
          <>
            <Facts
              items={[
                ["Digest", <Hash key="d" value={exp.data.digest} />],
                ["Signature", <Mono key="s">{exp.data.signature_status}</Mono>],
              ]}
            />
            <JsonView value={exp.data.content} label="Bundle content" />
          </>
        ) : null}
      </div>
    </Dialog>
  );
}

export function ClaimsPage() {
  const [audience, setAudience] = useState<string | null>(null);
  const q = useCellQuery([...REG.claims, audience], (api) => api.regulatory.claims(audience ?? undefined));
  const [exporting, setExporting] = useState<string | null>(null);
  const columns: Column<ClaimJudgement>[] = [
    { key: "id", header: "Claim", pinned: true, render: (c) => <Mono>{`${c.template_id} v${c.version}`}</Mono>, sortValue: (c) => c.template_id },
    { key: "text", header: "Wording", render: (c) => c.text },
    { key: "where", header: "Where", render: (c) => `${c.countries.join(", ")} · ${c.audiences.join(", ")}` },
    { key: "now", header: "Today", render: (c) => <Verdict ok={c.publishable} yes="May be published" no="Must not be published" />, sortValue: (c) => Number(c.publishable) },
    { key: "why", header: "Why not", render: (c) => <Reasons reasons={c.reasons} none="-" /> },
    {
      key: "proof",
      header: "Actions",
      align: "end",
      render: (c) => (
        <Button size="xs" variant="secondary" onPress={() => setExporting(`claim:${c.template_id}`)} aria-label={`Export proof for ${c.template_id}`}>
          Export proof
        </Button>
      ),
    },
  ];
  return (
    <>
      <PageHeader
        title="Regulated claims"
        description="Every public statement about certification or regulation (WF-07) comes from an approved template and is published only while its evidence holds today."
      />
      <Card
        padded={false}
        title="Claims"
        actions={
          <Select label="Audience" value={audience} onChange={setAudience} options={AUDIENCES.map((a) => ({ id: a, label: a }))} placeholder="All audiences" hideLabel />
        }
      >
        <QueryView query={q}>
          {(d) => (
            <DataTable
              caption="Regulated claims"
              columns={columns}
              rows={d.items}
              rowKey={(c) => `${c.template_id}:${c.version}`}
              empty={
                <EmptyState icon={Megaphone} title="No claim templates">
                  No regulated wording has been approved, so no certification claim may be published anywhere.
                </EmptyState>
              }
            />
          )}
        </QueryView>
      </Card>
      <ExportDialog bundleId={exporting} onClose={() => setExporting(null)} />
    </>
  );
}
