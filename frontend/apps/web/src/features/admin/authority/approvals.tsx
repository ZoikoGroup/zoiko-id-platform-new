"use client";

import { ApprovalsWaiting } from "@/features/approvals/guided-approval";
import type { ApprovalListItem, ApprovalState } from "@zoiko/api-client";
import { Button, Card, Code, CopyId, DataTable, DateTime, EmptyState, FilterBar, RelativeTime, StatusBadge, TextField, humanize, type Column } from "@zoiko/ui";
import { Plus, Search, Stamp } from "lucide-react";
import { useState, type FormEvent } from "react";

import { QueryView, useCellMutation, useCellQuery } from "@/lib/cell";
import { useRuntime } from "@/lib/runtime";

import { APPROVALS_KEY, ApprovalOutcome, CreateApprovalDialog, DecideApprovalDialog } from "./approval-dialogs";
import { UUID_RE } from "./shared";
import { TransactionContextField } from "./transaction-context";

const STATES: ApprovalState[] = ["PENDING", "APPROVED", "DENIED", "EXPIRED", "STALE"];

export function ApprovalsPanel() {
  const [filters, setFilters] = useState<Record<string, string>>({ state: "PENDING" });
  const q = { state: (filters.state || undefined) as ApprovalState | undefined };
  const approvals = useCellQuery([...APPROVALS_KEY, q], (api) => api.authority.approvals(q));
  const [creating, setCreating] = useState(false);
  const [deciding, setDeciding] = useState<ApprovalListItem | "byId" | null>(null);
  const { session } = useRuntime();
  const me = session?.authenticated ? session.subject.sub : null;
  // A request that says what it is for is decided in one click; the server picks the grant and rebuilds the binding.
  const decide = useCellMutation(
    (api, v: { id: string; verdict: "APPROVE" | "DENY" }) => api.authority.decideApproval(v.id, { verdict: v.verdict, risk_class: 3 }),
    { success: (r) => (r.state === "APPROVED" ? "Approved" : "Your decision was recorded"), invalidate: [APPROVALS_KEY, ["approvals"]] },
  );

  const columns: Column<ApprovalListItem>[] = [
    { key: "id", header: "Request", pinned: true, render: (a) => <CopyId value={a.approval_request_id} label="approval request" /> },
    {
      key: "subject",
      header: "What it is for",
      render: (a) => (a.summary ? <span className="text-sm text-ink">{a.summary}</span> : <Code>{a.subject_ref}</Code>),
      sortValue: (a) => a.summary ?? a.subject_ref,
    },
    { key: "state", header: "State", render: (a) => <StatusBadge state={a.state} kind="Approval" />, sortValue: (a) => a.state },
    { key: "mode", header: "Mode", render: (a) => humanize(a.mode), sortValue: (a) => a.mode },
    { key: "roles", header: "Roles", render: (a) => <span className="font-mono text-xs">{a.roles.join(", ")}</span> },
    {
      key: "progress",
      header: "Approvals",
      render: (a) => (
        <span className="tabular text-sm" aria-label={`${a.approvals_received} of ${a.required_approvals} approvals received`}>
          {a.approvals_received}/{a.required_approvals}
        </span>
      ),
      sortValue: (a) => a.required_approvals - a.approvals_received,
    },
    { key: "requester", header: "Requested by", render: (a) => <CopyId value={a.requester_principal_id} label="requester" />, defaultHidden: true },
    { key: "created", header: "Opened", render: (a) => <RelativeTime value={a.created_at} />, sortValue: (a) => a.created_at },
    { key: "expires", header: "Expires", render: (a) => <DateTime value={a.expires_at} />, sortValue: (a) => a.expires_at },
    {
      key: "codes",
      header: "Codes",
      render: (a) => (a.codes.length ? <span className="font-mono text-xs">{a.codes.join(", ")}</span> : <span className="text-ink-subtle">—</span>),
      defaultHidden: true,
    },
    {
      key: "row-actions",
      header: "Row actions",
      align: "end",
      render: (a) => {
        if (a.state !== "PENDING") return null;
        if (a.summary && a.action) {
          if (a.requester_principal_id === me) return <span className="text-xs text-ink-muted">Waiting for someone else</span>;
          return (
            <span className="flex justify-end gap-2">
              <Button size="xs" variant="secondary" isDisabled={decide.isPending} onPress={() => decide.mutate({ id: a.approval_request_id, verdict: "DENY" })}>
                Decline
              </Button>
              <Button size="xs" isDisabled={decide.isPending} onPress={() => decide.mutate({ id: a.approval_request_id, verdict: "APPROVE" })}>
                Approve
              </Button>
            </span>
          );
        }
        return (
          <Button size="xs" variant="secondary" onPress={() => setDeciding(a)}>
            Decide (advanced)
          </Button>
        );
      },
    },
  ];

  return (
    <div className="space-y-5">
      <ApprovalsWaiting />
      <Card
        title="Approval requests"
        description="Changes that need a second administrator. Ask from the page that makes the change, and approve here or in the box above. The buttons marked advanced are for requests opened with an exact transaction context."
        actions={
          <>
            <Button variant="secondary" onPress={() => setDeciding("byId")}>
              Decide by ID (advanced)
            </Button>
            <Button icon={<Plus aria-hidden className="size-4" />} onPress={() => setCreating(true)}>
              New request (advanced)
            </Button>
          </>
        }
        padded={false}
      >
        <div className="border-b border-border px-5 py-3">
          <FilterBar
            filters={[{ id: "state", label: "State", options: STATES.map((s) => ({ id: s, label: humanize(s) })) }]}
            values={filters}
            onChange={setFilters}
          />
        </div>
        <QueryView query={approvals}>
          {(data) => (
            <DataTable
              caption="Approval requests"
              columns={columns}
              rows={data.items}
              rowKey={(a) => a.approval_request_id}
              empty={
                <EmptyState icon={Stamp} title={filters.state ? `No ${humanize(filters.state).toLowerCase()} requests` : "No approval requests"}>
                  {filters.state === "PENDING" ? "Nothing is waiting for a decision." : "No requests match this state."}
                </EmptyState>
              }
            />
          )}
        </QueryView>
      </Card>

      <ApprovalLookup />

      {creating ? <CreateApprovalDialog onClose={() => setCreating(false)} /> : null}
      {deciding ? <DecideApprovalDialog target={deciding === "byId" ? null : deciding} onClose={() => setDeciding(null)} /> : null}
    </div>
  );
}

/** Reading a request needs its transaction context: the outcome is for that exact transaction. */
function ApprovalLookup() {
  const [id, setId] = useState("");
  const [tc, setTc] = useState("");
  const [query, setQuery] = useState<{ id: string; tc: string } | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const view = useCellQuery([...APPROVALS_KEY, "one", query], (api) => api.authority.approval(query?.id ?? "", query?.tc ?? ""), {
    enabled: query !== null,
  });

  function submit(e: FormEvent) {
    e.preventDefault();
    if (!UUID_RE.test(id.trim())) return setProblem("Enter an approval request ID.");
    if (!tc.trim()) return setProblem("Enter the transaction context.");
    setProblem(null);
    setQuery({ id: id.trim(), tc });
  }

  return (
    <Card title="Check a request" description="See the current outcome of a request for one exact transaction context.">
      <form onSubmit={submit} className="space-y-4" role="search" aria-label="Check an approval request">
        <TextField label="Approval request ID" isRequired value={id} onChange={setId} mono />
        <TransactionContextField value={tc} onChange={setTc} />
        {problem ? <p className="text-sm font-medium text-danger-fg">{problem}</p> : null}
        <div className="flex justify-end">
          <Button type="submit" variant="secondary" icon={<Search aria-hidden className="size-4" />}>
            Check outcome
          </Button>
        </div>
      </form>
      {query ? (
        <div className="mt-5 border-t border-border pt-5">
          <QueryView
            query={view}
            loadingRows={2}
            notFound={
              <EmptyState icon={Stamp} title="No matching request" compact>
                There is no request with this ID, or it was opened for a different transaction context.
              </EmptyState>
            }
          >
            {(v) => <ApprovalOutcome view={v} />}
          </QueryView>
        </div>
      ) : null}
    </Card>
  );
}
