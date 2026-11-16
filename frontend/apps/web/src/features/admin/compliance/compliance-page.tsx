"use client";

import type { SofQueueItem, WfRunSummary } from "@zoiko/api-client";
import { Badge, Banner, DataTable, DateTime, EmptyState, PageHeader } from "@zoiko/ui";

import { useCellQuery } from "@/lib/cell";

import { SofQuery } from "../source-of-funds/shared";

export const COMPLIANCE_KEY = ["compliance"];

/** What a run is waiting for, in words. A run waiting for a check is not waiting for a person, so it is not listed here. */
export function runWaitText(r: Pick<WfRunSummary, "status" | "awaiting_role">): string {
  return r.status === "WAITING_FOR_REVIEW" ? `Waiting for a ${(r.awaiting_role ?? "reviewer").replaceAll("_", " ")}` : "Not waiting for a person";
}

/** One place for the company's own work that is waiting for a person. It only reads what each source already lets this person read: a source the
 * role may not read says Withheld, not none, and nothing here belongs to another company or to the operator side. */
export function CompliancePage() {
  const sof = useCellQuery([...COMPLIANCE_KEY, "sof"], (api) => api.sourceOfFunds.queue("NEEDS_REVIEW"));
  const runs = useCellQuery([...COMPLIANCE_KEY, "runs"], (api) => api.workflows.runs("WAITING_FOR_REVIEW"));
  return (
    <>
      <PageHeader
        title="Compliance cases"
        description="Your company's own cases that wait for a person. The system never decides one of them: each moves only when a person rules."
        breadcrumbs={[{ label: "Compliance cases" }]}
      />
      <div className="space-y-8">
        <Banner tone="info" title="A person decides">
          This page lists work and opens it. A case closes only on a reviewer's decision, on its own page.
        </Banner>

        <section aria-labelledby="cc-sof" className="space-y-3">
          <h2 id="cc-sof" className="text-base font-semibold text-ink">
            Source of funds and wealth
          </h2>
          <SofQuery query={sof}>
            {(data) => (
              <DataTable<SofQueueItem>
                caption="Source of funds cases waiting for review"
                rows={data.items}
                rowKey={(r) => r.case_id}
                rowHref={(r) => `/admin/source-of-funds/${encodeURIComponent(r.case_id)}`}
                empty="No source of funds cases are waiting."
                columns={[
                  { key: "case", header: "Case", pinned: true, render: (r) => <span className="font-mono text-xs">{r.case_id.slice(0, 8)}</span> },
                  { key: "state", header: "State", render: (r) => <Badge tone="warn">{r.state.replaceAll("_", " ").toLowerCase()}</Badge> },
                  { key: "country", header: "Country", render: (r) => r.country },
                  { key: "when", header: "Submitted", render: (r) => (r.submitted_at ? <DateTime value={r.submitted_at} /> : "Not submitted") },
                ]}
              />
            )}
          </SofQuery>
        </section>

        <section aria-labelledby="cc-runs" className="space-y-3">
          <h2 id="cc-runs" className="text-base font-semibold text-ink">
            Verification workflow runs
          </h2>
          <SofQuery
            query={runs}
            forbidden={<EmptyState title="Withheld for your role">Your role does not include reading verification workflow runs. This is not an empty result.</EmptyState>}
          >
            {(data) => (
              <DataTable<WfRunSummary>
                caption="Verification workflow runs waiting for a person"
                rows={data.items}
                rowKey={(r) => r.run_id}
                rowHref={(r) => `/admin/workflows/${encodeURIComponent(r.workflow_id)}/${encodeURIComponent(r.version)}`}
                empty="No workflow runs are waiting."
                columns={[
                  { key: "run", header: "Run", pinned: true, render: (r) => <span className="font-mono text-xs">{r.run_id.slice(0, 8)}</span> },
                  { key: "wf", header: "Workflow", render: (r) => `${r.workflow_id} ${r.version}` },
                  { key: "wait", header: "Waiting for", render: (r) => <Badge tone="warn">{runWaitText(r)}</Badge> },
                  { key: "when", header: "Started", render: (r) => <DateTime value={r.created_at} /> },
                ]}
              />
            )}
          </SofQuery>
        </section>
      </div>
    </>
  );
}
