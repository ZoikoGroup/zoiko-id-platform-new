"use client";

import type { SofQueueItem } from "@zoiko/api-client";
import { Badge, Banner, DataTable, DateTime, PageHeader, Select } from "@zoiko/ui";
import { useState } from "react";

import { useCellQuery } from "@/lib/cell";

import { FILTERS, SOF_KEY, stateText } from "./logic";
import { SofQuery } from "./shared";

/** The source of funds review queue. It lists cases, never answers: reading the answers is a separate permission on the case page. */
export function SourceOfFundsQueuePage() {
  const [state, setState] = useState("");
  const queue = useCellQuery([...SOF_KEY, "queue", state], (api) => api.sourceOfFunds.queue(state || undefined));
  return (
    <>
      <PageHeader
        title="Source of funds and wealth"
        description="Cases waiting for a person. The system never clears anyone: every outcome is a reviewer's decision."
        breadcrumbs={[{ label: "Source of funds" }]}
      />
      <SofQuery query={queue}>
        {(data) => (
          <div className="space-y-4">
            <Banner tone="info" title="A person decides">
              {data.notice}
            </Banner>
            <Select label="Show" options={FILTERS.map((f) => ({ id: f.id, label: f.label }))} value={state} onChange={setState} />
            <DataTable<SofQueueItem>
              caption="Source of funds cases"
              rows={data.items}
              rowKey={(r) => r.case_id}
              rowHref={(r) => `/admin/source-of-funds/${encodeURIComponent(r.case_id)}`}
              empty="No cases match this filter."
              columns={[
                { key: "case", header: "Case", pinned: true, render: (r) => <span className="font-mono text-xs">{r.case_id.slice(0, 8)}</span> },
                { key: "state", header: "State", render: (r) => <StateBadge row={r} /> },
                { key: "country", header: "Country", render: (r) => r.country },
                { key: "version", header: "Questionnaire", render: (r) => r.questionnaire_version },
                { key: "submitted", header: "Submitted", render: (r) => (r.submitted_at ? <DateTime value={r.submitted_at} /> : "Not sent") },
              ]}
            />
          </div>
        )}
      </SofQuery>
    </>
  );
}

function StateBadge({ row }: { row: SofQueueItem }) {
  const s = stateText(row.state, row.valid_until);
  return <Badge tone={s.tone}>{s.label}</Badge>;
}
