"use client";

import type { ApprovalScope, OrgApprovalSummary } from "@zoiko/api-client";
import { Button, Card, EmptyState, LoadingRows, PageHeader, Select, Tabs, TextField } from "@zoiko/ui";
import { ClipboardCheck } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { ApiErrorView, QueryView, useCellMutation, useCellQuery } from "@/lib/cell";

import {
  GOVERNANCE_HREF,
  RISK_OPTIONS,
  STATE_FILTER_OPTIONS,
  TABS,
  countsAnnouncement,
  hasFilters,
  inboxQuery,
  noFilters,
  setupHref,
  tabLabel,
  type InboxFilters,
} from "./logic";
import { NewRequestDialog } from "./new-request";
import { RequestCard } from "./parts";
import { useOrgId } from "./shared";

/** WF-13C-02: what this person may review, what they submitted, and what ended. Built from object-level authorization on the server,
 * so a filter or a count can show nothing the person could not open. */
export function InboxPage({ orgParam }: { orgParam: string }) {
  const { orgId, loading, error } = useOrgId(orgParam);
  const [scope, setScope] = useState<ApprovalScope>("review");
  const [draft, setDraft] = useState<InboxFilters>(noFilters);
  const [filters, setFilters] = useState<InboxFilters>(noFilters);
  const [more, setMore] = useState<{ key: string; items: OrgApprovalSummary[]; cursor: string | null } | null>(null);
  const [asking, setAsking] = useState(false);
  const key = `${scope}|${JSON.stringify(filters)}`;

  const first = useCellQuery(["org-approvals", orgId, key], (api) => api.org.approvals(orgId as string, inboxQuery(scope, filters)), {
    enabled: Boolean(orgId),
    refetchInterval: 20_000,
  });
  const loadMore = useCellMutation((api, cursor: string) => api.org.approvals(orgId as string, inboxQuery(scope, filters, cursor)), {
    onSuccess: (page) => setMore((m) => ({ key, items: [...(m?.key === key ? m.items : []), ...page.items], cursor: page.next_cursor })),
  });

  if (!orgId) {
    if (loading) return <LoadingRows rows={4} />;
    return error ? <ApiErrorView error={error} /> : <EmptyState title="No organization">Sign in to an organization to see its approvals.</EmptyState>;
  }
  const extra = more?.key === key ? more : null;
  const body = (
    <QueryView query={first}>
      {(d) => {
        const items = [...d.items, ...(extra?.items ?? [])];
        const cursor = extra ? extra.cursor : d.next_cursor;
        return (
          <>
            <p role="status" aria-live="polite" className="sr-only">
              {countsAnnouncement(d.counts, scope, items.length)}
            </p>
            {items.length === 0 ? (
              <EmptyState title={hasFilters(filters) ? "Nothing matches these filters" : "Nothing here"}>
                {scope === "review"
                  ? "Requests you can approve appear here. A request you made yourself never does."
                  : scope === "mine"
                    ? "Requests you submit appear here."
                    : "Requests that ended appear here."}
              </EmptyState>
            ) : (
              <ul className="space-y-3" aria-label={TABS.find((t) => t.id === scope)?.label}>
                {items.map((r) => (
                  <RequestCard key={r.request_id} orgId={orgId} r={r} />
                ))}
              </ul>
            )}
            {cursor ? (
              <div className="mt-4">
                <Button variant="secondary" className="min-h-11" pending={loadMore.isPending} onPress={() => loadMore.mutate(cursor)}>
                  Load more
                </Button>
              </div>
            ) : null}
            {loadMore.error ? <div className="mt-3"><ApiErrorView error={loadMore.error} title="More requests could not be loaded" /></div> : null}
          </>
        );
      }}
    </QueryView>
  );

  const counts = first.data?.counts;
  return (
    <div className="space-y-6">
      <PageHeader
        icon={ClipboardCheck}
        title="Approvals"
        description="Sensitive and critical changes need independent people. A critical change needs two people other than the one who asked. Nothing changes until an approved request is applied."
        breadcrumbs={[{ label: "Company" }, { label: "Approvals" }]}
        actions={
          <span className="flex flex-wrap items-center gap-3 text-sm">
            <Button className="min-h-11" onPress={() => setAsking(true)}>
              New request
            </Button>
            <Link className="inline-flex min-h-11 items-center text-brand-700 underline-offset-2 hover:underline" href={GOVERNANCE_HREF}>
              Governance and reviewers
            </Link>
            <Link className="inline-flex min-h-11 items-center text-brand-700 underline-offset-2 hover:underline" href={setupHref(orgId)}>
              Organization setup
            </Link>
          </span>
        }
      />
      <Card title="Filter">
        <form
          className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4"
          onSubmit={(e) => {
            e.preventDefault();
            setFilters(draft);
            setMore(null);
          }}
        >
          <Select label="Level" value={draft.risk} onChange={(v) => setDraft((f) => ({ ...f, risk: v }))} options={RISK_OPTIONS} />
          <Select label="State" value={draft.state} onChange={(v) => setDraft((f) => ({ ...f, state: v }))} options={STATE_FILTER_OPTIONS} />
          <TextField label="Created since" type="date" value={draft.from} onChange={(v) => setDraft((f) => ({ ...f, from: v }))} inputClassName="min-h-11" />
          <TextField label="Request id" value={draft.q} onChange={(v) => setDraft((f) => ({ ...f, q: v }))} mono inputClassName="min-h-11" description="The start of a request id." />
          <div className="flex flex-wrap gap-2 sm:col-span-2 lg:col-span-4">
            <Button type="submit" className="min-h-11">
              Apply filters
            </Button>
            <Button
              variant="secondary"
              className="min-h-11"
              isDisabled={!hasFilters(draft) && !hasFilters(filters)}
              onPress={() => {
                setDraft(noFilters);
                setFilters(noFilters);
                setMore(null);
              }}
            >
              Clear
            </Button>
          </div>
        </form>
      </Card>
      <NewRequestDialog orgId={orgId} isOpen={asking} onOpenChange={setAsking} />
      <Tabs
        label="Approval requests"
        selectedTab={scope}
        onSelectionChange={(id) => {
          setScope(id as ApprovalScope);
          setMore(null);
        }}
        tabs={TABS.map((t) => ({ id: t.id, label: tabLabel(t, counts), content: scope === t.id ? body : null }))}
      />
    </div>
  );
}
