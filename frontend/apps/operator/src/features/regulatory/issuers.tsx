"use client";

import type { IssuerStatement } from "@zoiko/api-client";
import { Badge, Button, Card, DataTable, DateTime, EmptyState, JsonView, PageHeader, Select, TextField, type Column } from "@zoiko/ui";
import { Landmark } from "lucide-react";
import { useState } from "react";

import { QueryView, useCellQuery } from "@/lib/op";

import { Hash, isoFromLocal, Mono, REG, Reasons, Verdict } from "./common";

function AwardCheck({ issuer }: { issuer: string }) {
  const [f, setF] = useState({ program: "", awarded_on: "", known_at: "" });
  const [asked, setAsked] = useState<typeof f | null>(null);
  const q = useCellQuery(
    ["regulatory", "award-check", issuer, asked],
    (api) => api.regulatory.awardCheck(issuer, asked!.program, asked!.awarded_on, asked!.known_at ? isoFromLocal(asked!.known_at) : undefined),
    { enabled: asked !== null },
  );
  return (
    <Card title="Was this issuer trusted when the award was made?" description="Judged against what the ledger knew at the time you choose (default now). Unknown history is REVIEW, never PASS.">
      <form
        className="flex flex-wrap items-end gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          setAsked({ ...f });
        }}
      >
        <TextField label="Programme" value={f.program} onChange={(v) => setF((p) => ({ ...p, program: v }))} isRequired />
        <TextField label="Awarded on" type="date" value={f.awarded_on} onChange={(v) => setF((p) => ({ ...p, awarded_on: v }))} isRequired />
        <TextField label="As known at" type="datetime-local" value={f.known_at} onChange={(v) => setF((p) => ({ ...p, known_at: v }))} />
        <Button type="submit" variant="secondary" isDisabled={!f.program || !f.awarded_on}>
          Check
        </Button>
      </form>
      {asked ? (
        <div className="mt-3">
          <QueryView query={q}>
            {(d) => (
              <div className="flex flex-wrap items-center gap-2 text-sm">
                <Verdict ok={d.outcome === "PASS"} yes="Pass" no="Review" />
                <Badge>{d.accreditation}</Badge>
                {d.name_at_award ? <span>named {d.name_at_award} at award</span> : null}
                <Reasons reasons={d.reasons} none="No concerns" />
              </div>
            )}
          </QueryView>
        </div>
      ) : null}
    </Card>
  );
}

export function IssuersPage() {
  const issuers = useCellQuery(REG.issuers, (api) => api.regulatory.issuers());
  const [issuer, setIssuer] = useState<string | null>(null);
  const history = useCellQuery([...REG.issuers, issuer], (api) => api.regulatory.issuerStatements(issuer!), { enabled: issuer !== null });
  const columns: Column<IssuerStatement>[] = [
    { key: "seq", header: "#", pinned: true, render: (s) => s.seq, sortValue: (s) => s.seq },
    { key: "kind", header: "Statement", render: (s) => <Badge tone={s.statement === "CORRECTION" ? "warn" : "neutral"}>{s.statement}</Badge> },
    { key: "record", header: "What it says", render: (s) => <JsonView value={s.record} label="Record" /> },
    { key: "reason", header: "Correction reason", render: (s) => s.reason ?? "-" },
    { key: "source", header: "Source", render: (s) => <Hash value={s.source_hash} /> },
    { key: "by", header: "Recorded", render: (s) => <span><Mono>{s.recorded_by}</Mono> · <DateTime value={s.recorded_at} /></span> },
  ];
  return (
    <>
      <PageHeader
        title="Issuers"
        description="Credential issuers' history (WF-04): append-only statements and corrections, so a past award is judged by what was true and known then, not by today's list."
      />
      <div className="space-y-5">
        <QueryView query={issuers}>
          {(d) =>
            d.items.length ? (
              <Select label="Issuer" value={issuer} onChange={setIssuer} options={d.items.map((i) => ({ id: i, label: i }))} className="max-w-sm" />
            ) : (
              <EmptyState icon={Landmark} title="No issuer history">
                No statement has been recorded for any issuer. Statements are recorded through the regulatory API.
              </EmptyState>
            )
          }
        </QueryView>
        {issuer ? (
          <>
            <Card padded={false} title={`History of ${issuer}`}>
              <QueryView query={history}>
                {(d) => <DataTable caption="Issuer statements" columns={columns} rows={d.items} rowKey={(s) => s.statement_id} />}
              </QueryView>
            </Card>
            <AwardCheck issuer={issuer} />
          </>
        ) : null}
      </div>
    </>
  );
}
