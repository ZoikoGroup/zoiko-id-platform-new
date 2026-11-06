"use client";

import type { ServiceRequest, AssuranceException, AuditPackVerdict, OperatorApi, PaymentEvent, PlanRequest, PriceBookSummary } from "@zoiko/api-client";
import { Badge, Banner, Button, Card, DataTable, EmptyState, JsonView, StatusBadge, TextField, type Column } from "@zoiko/ui";
import { BookOpen, CreditCard, Inbox, ShieldCheck } from "lucide-react";
import { useState, type ReactNode } from "react";

import { QueryView, useCellMutation, useCellQuery } from "@/lib/op";

import { CapabilityHint } from "../recovery-shared";
import { useDirectMode } from "./mode";
import { PriceBookEditor } from "./price-book-editor";

import { COMMERCIAL_KEY } from "./keys";

export { COMMERCIAL_KEY };

export function PriceBooksView() {
  const books = useCellQuery([...COMMERCIAL_KEY, "books"], (api) => api.commercial.books());
  const [picked, setPicked] = useState<string | null>(null);
  const items = books.data?.items ?? [];
  // Something is always open: the editable draft if there is one, else the newest book, so the rates are visible without a click.
  const open = picked ?? items.find((b) => b.status === "DRAFT")?.price_book_id ?? items[0]?.price_book_id ?? null;
  const columns: Column<PriceBookSummary>[] = [
    { key: "v", header: "Version", pinned: true, render: (b) => `v${b.version}`, sortValue: (b) => b.version },
    { key: "s", header: "Status", render: (b) => <StatusBadge state={b.status} kind="Price book" /> },
    { key: "c", header: "Currency", render: (b) => b.currency },
    { key: "e", header: "Effective from", render: (b) => (b.effective_from ? new Date(b.effective_from).toLocaleString("en-GB") : "—") },
    { key: "n", header: "Note", render: (b) => b.note ?? "—" },
    { key: "id", header: "ID", defaultHidden: true, render: (b) => <span className="font-mono text-2xs">{b.price_book_id}</span> },
  ];
  return (
    <div className="space-y-4">
      <QueryView query={books}>
        {(d) => (
          <Card title="Price books" description="Select a row to see its rates. An approved book is frozen by the database." padded={false}>
            <DataTable<PriceBookSummary>
              caption="Price books, newest first"
              columns={columns}
              rows={d.items}
              rowKey={(b) => b.price_book_id}
              onRowAction={(b) => setPicked(b.price_book_id)}
              empty={<EmptyState title="No price books" icon={BookOpen}>Create a draft below.</EmptyState>}
            />
          </Card>
        )}
      </QueryView>
      {open ? <PriceBookEditor id={open} onOpen={setPicked} /> : null}
    </div>
  );
}

type OpenPlanRequest = PlanRequest & { tenant_id: string; company: string | null };

/** What companies have asked for. Accepting says the team will raise the order; it moves no entitlement. */
export function PlanRequestsView() {
  const direct = useDirectMode();
  const list = useCellQuery([...COMMERCIAL_KEY, "plan-requests"], (api) => api.commercial.planRequests());
  const [note, setNote] = useState("");
  const answer = useCellMutation<{ id: string; decision: "ACCEPTED" | "DECLINED" }, unknown>(
    (api, v) => api.commercial.answerPlanRequest(v.id, v.decision, note),
    { invalidate: [COMMERCIAL_KEY], success: "Answer recorded" },
  );
  // Direct mode: accept the request and put the company on the plan in one step, so the operator never copies an id.
  const move = useCellMutation<OpenPlanRequest, unknown>(
    async (api, r) => {
      await api.commercial.answerPlanRequest(r.request_id, "ACCEPTED", note);
      return api.commercial.call("PUT", `/v1/commercial/tenants/${r.tenant_id}/entitlement`, { plan: r.plan });
    },
    { invalidate: [COMMERCIAL_KEY], success: "Accepted, and the company is now on the new plan" },
  );
  const columns: Column<OpenPlanRequest>[] = [
    {
      key: "t",
      header: "Company",
      pinned: true,
      render: (r) => (
        <span className="flex flex-col">
          <span className="font-medium">{r.company ?? "Unnamed company"}</span>
          <span className="font-mono text-2xs text-ink-subtle">{r.tenant_id}</span>
        </span>
      ),
      sortValue: (r) => r.company ?? r.tenant_id,
    },
    { key: "p", header: "Wants", render: (r) => <Badge tone="info">{r.plan}</Badge>, sortValue: (r) => r.plan },
    { key: "c", header: "Has", render: (r) => r.current_plan },
    { key: "n", header: "Their note", render: (r) => r.note ?? "—" },
    { key: "w", header: "Asked", render: (r) => new Date(r.created_at).toLocaleString("en-GB"), sortValue: (r) => r.created_at },
    {
      key: "a",
      header: "Answer",
      align: "end",
      render: (r) => (
        <span className="flex justify-end gap-2">
          <Button size="xs" variant="secondary" isDisabled={answer.isPending} onPress={() => answer.mutate({ id: r.request_id, decision: "DECLINED" })}>
            Decline
          </Button>
          {direct ? (
            <Button size="xs" isDisabled={answer.isPending || move.isPending} onPress={() => move.mutate(r)}>
              Accept and move to {r.plan}
            </Button>
          ) : (
            <Button size="xs" isDisabled={answer.isPending} onPress={() => answer.mutate({ id: r.request_id, decision: "ACCEPTED" })}>
              Accept
            </Button>
          )}
        </span>
      ),
    },
  ];
  return (
    <QueryView query={list}>
      {(d) => (
        <Card
          title="Plan requests"
          description={
            direct
              ? "Companies asking for a different plan, oldest first. Accept and move puts the company on the plan straight away. The note below goes to the company."
              : "Companies asking for a different plan, oldest first. Accepting means we will raise the order; it changes no entitlement. The note below goes to the company."
          }
          padded={false}
        >
          <div className="p-4">
            <TextField label="Note to the company (optional)" value={note} onChange={setNote} />
          </div>
          <DataTable<OpenPlanRequest>
            caption="Open plan requests"
            columns={columns}
            rows={d.items}
            rowKey={(r) => r.request_id}
            empty={<EmptyState title="No open requests" icon={Inbox} compact>When a company asks for a plan it appears here.</EmptyState>}
          />
          {move.isError ? <div className="p-4"><Banner tone="danger">{move.error instanceof Error ? move.error.message : "The company was not moved."}</Banner></div> : null}
          {answer.isError ? <div className="p-4"><Banner tone="danger">{answer.error instanceof Error ? answer.error.message : "The answer was not recorded."}</Banner></div> : null}
        </Card>
      )}
    </QueryView>
  );
}

export function ExceptionsView() {
  const list = useCellQuery([...COMMERCIAL_KEY, "exceptions"], (api) => api.commercial.exceptions());
  const columns: Column<AssuranceException>[] = [
    { key: "k", header: "Kind", pinned: true, render: (e) => <Badge tone="warn">{e.kind}</Badge>, sortValue: (e) => e.kind },
    { key: "r", header: "Reference", render: (e) => <span className="font-mono text-xs">{e.ref}</span> },
    { key: "t", header: "Tenant", render: (e) => (e.tenant_id ? <span className="font-mono text-2xs">{e.tenant_id}</span> : "Platform") },
    { key: "w", header: "Raised", render: (e) => new Date(e.raised_at).toLocaleString("en-GB"), sortValue: (e) => e.raised_at },
    { key: "id", header: "Exception ID", render: (e) => <span className="font-mono text-2xs">{e.exception_id}</span> },
  ];
  return (
    <QueryView query={list}>
      {(d) => (
        <Card title="Open exceptions" description="One open per kind and reference. Resolve one under Assurance actions with the ID shown." padded={false}>
          <DataTable<AssuranceException>
            caption="Open revenue-assurance exceptions"
            columns={columns}
            rows={d.items}
            rowKey={(e) => e.exception_id}
            empty={<EmptyState title="No open exceptions" icon={ShieldCheck}>Run the detectors to check again.</EmptyState>}
          />
        </Card>
      )}
    </QueryView>
  );
}

const EVENT_LABEL: Record<string, string> = {
  "charge.refunded": "Refund",
  "charge.dispute.created": "Dispute opened",
  "charge.dispute.closed": "Dispute closed",
  "charge.dispute.funds_withdrawn": "Dispute funds withdrawn",
  "payment_intent.succeeded": "Payment received",
  "payment_intent.payment_failed": "Payment failed",
};

/** What the payment provider reported that no rule could settle by itself. Work an item with the actions below. */
export function PaymentEventsView() {
  const list = useCellQuery([...COMMERCIAL_KEY, "payment-events"], (api) => api.commercial.paymentEvents());
  const columns: Column<PaymentEvent>[] = [
    { key: "k", header: "What happened", pinned: true, render: (e) => <Badge tone="warn">{EVENT_LABEL[e.event_type] ?? e.event_type}</Badge>, sortValue: (e) => e.event_type },
    { key: "o", header: "Why it is here", render: (e) => (e.outcome === "UNMATCHED" ? "Matches no invoice of the customer" : "Needs a decision") },
    { key: "a", header: "Amount", render: (e) => (e.amount ? `${e.amount} ${e.currency ?? ""}` : "—") },
    { key: "t", header: "Tenant", render: (e) => (e.tenant_id ? <span className="font-mono text-2xs">{e.tenant_id}</span> : "Not identified") },
    { key: "i", header: "Invoice", render: (e) => (e.invoice_id ? <span className="font-mono text-2xs">{e.invoice_id}</span> : "—") },
    { key: "w", header: "Received", render: (e) => new Date(e.received_at).toLocaleString("en-GB"), sortValue: (e) => e.received_at },
    { key: "id", header: "Event ID", render: (e) => <span className="font-mono text-2xs">{e.event_id}</span> },
  ];
  return (
    <QueryView query={list}>
      {(d) => (
        <Card
          title="Payment events waiting for a person"
          description="Refunds, disputes and payments that match no invoice. A refund can be credited here; anything else is closed with a note of what was done."
          padded={false}
        >
          <DataTable<PaymentEvent>
            caption="Payment provider events awaiting review"
            columns={columns}
            rows={d.items}
            rowKey={(e) => e.event_id}
            empty={<EmptyState title="Nothing waiting" icon={CreditCard}>Refunds, disputes and unmatched payments appear here when Stripe reports them.</EmptyState>}
          />
        </Card>
      )}
    </QueryView>
  );
}

/** A read the operator triggers on demand; the answer is shown as the cell returned it. */
function Reader({ title, description, run, children }: { title: string; description: string; run: (api: OperatorApi) => Promise<unknown>; children?: ReactNode }) {
  const m = useCellMutation<void, unknown>((api) => run(api));
  return (
    <Card title={title} description={description}>
      {children}
      <Button variant="secondary" onPress={() => m.mutate(undefined)} isDisabled={m.isPending}>
        {m.isPending ? "Reading…" : title}
      </Button>
      <div aria-live="polite" className="mt-3 space-y-3">
        {m.isError ? <CapabilityHint error={m.error} capability="commercial.billing.manage" /> : null}
        {m.isError ? <Banner tone="danger">{m.error instanceof Error ? m.error.message : "The read failed."}</Banner> : null}
        {m.isSuccess ? <JsonView value={m.data} label={`${title} result`} /> : null}
      </div>
    </Card>
  );
}

export function AuditView() {
  const [tenant, setTenant] = useState("");
  const [month, setMonth] = useState("");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  return (
    <div className="space-y-4">
      <div className="grid gap-4 lg:grid-cols-2">
        <Reader title="Verify the commercial change log" description="Recomputes every digest and link of the append-only chain." run={(a) => a.commercial.verifyChangeLog()} />
        <Reader title="Verify the invoice chain" description="Recomputes every issued invoice's digest and chain hash." run={(a) => a.commercial.verifyInvoiceChain()} />
      </div>
      <Reader title="Management KPIs" description="MRR and ARR from activated, non-pilot orders at their contracted price book; intercompany excluded." run={(a) => a.commercial.kpis()} />
      <Card title="Tenant evidence" description="Frozen-window hashes, rating hashes, invoice digests and the change log for one tenant-month, signed by the platform evidence key.">
        <div className="grid gap-3 sm:grid-cols-2">
          <TextField label="Tenant ID" value={tenant} onChange={setTenant} mono className="sm:col-span-2" />
          <TextField label="Month (YYYY-MM)" value={month} onChange={setMonth} placeholder="2026-10" />
          <span />
          <TextField label="Journal from" type="datetime-local" value={start} onChange={setStart} />
          <TextField label="Journal to" type="datetime-local" value={end} onChange={setEnd} />
        </div>
        <div className="mt-3 grid gap-4 lg:grid-cols-2">
          <Reader title="Audit pack" description="Needs a tenant and a month." run={(a) => a.commercial.auditPack(tenant.trim(), month.trim())} />
          <Reader
            title="Journal export"
            description="Balanced journal lines from issued invoices, credit notes and captured payments. Exported, never posted."
            run={(a) => a.commercial.journal(tenant.trim(), new Date(start).toISOString(), new Date(end).toISOString())}
          />
        </div>
      </Card>
      <Card title="Look up a quote" description="A quote by its ID, as the platform holds it: lines, discount authority and approval state.">
        <QuoteLookup />
      </Card>
      <VerifyAuditPack />
      <VendorCosts />
    </div>
  );
}

function QuoteLookup() {
  const [id, setId] = useState("");
  return (
    <Reader title="Look up quote" description="Needs the quote ID." run={(a) => a.commercial.quote(id.trim())}>
      <TextField label="Quote ID" value={id} onChange={setId} mono className="mb-3" />
    </Reader>
  );
}

/** Checks a pack that was exported earlier, or handed over by someone else, against the platform's evidence keys. */
function VerifyAuditPack() {
  const [text, setText] = useState("");
  const [verdict, setVerdict] = useState<AuditPackVerdict | null>(null);
  const [parseError, setParseError] = useState<string | null>(null);
  const check = useCellMutation<Record<string, unknown>, AuditPackVerdict>((api, pack) => api.commercial.verifyAuditPack(pack), {
    onSuccess: setVerdict,
  });
  function run() {
    setVerdict(null);
    setParseError(null);
    try {
      const parsed: unknown = JSON.parse(text);
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error();
      check.mutate(parsed as Record<string, unknown>);
    } catch {
      setParseError("Paste the audit pack exactly as it was exported: one JSON object.");
    }
  }
  return (
    <Card title="Verify an audit pack" description="Recomputes the signature, the body and the bundle digest of a pack against the trusted evidence keys. It proves the pack is unaltered, not that its contents are right.">
      <TextField label="Audit pack (JSON)" multiline rows={6} mono value={text} onChange={setText} />
      <div className="mt-3 flex items-center gap-3">
        <Button variant="secondary" isDisabled={!text.trim() || check.isPending} onPress={run}>
          {check.isPending ? "Checking…" : "Verify pack"}
        </Button>
      </div>
      <div aria-live="polite" className="mt-3 space-y-2">
        {parseError ? <Banner tone="danger">{parseError}</Banner> : null}
        {check.isError ? <CapabilityHint error={check.error} capability="commercial.billing.manage" /> : null}
        {verdict ? (
          verdict.valid ? (
            <Banner tone="ok" title="The pack verifies">
              Signed with key <span className="font-mono">{verdict.kid}</span>
              {verdict.issuer ? <> by <span className="font-mono">{verdict.issuer}</span></> : null}. Nothing in it has been changed since it was signed.
            </Banner>
          ) : (
            <Banner tone="danger" title="The pack does not verify">
              Reason: <span className="font-mono">{verdict.reason ?? "unknown"}</span>. Do not rely on it.
            </Banner>
          )
        ) : null}
      </div>
    </Card>
  );
}

function VendorCosts() {
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  return (
    <Reader
      title="Supplier cost report"
      description="Attempts, chargeable attempts and supplier cost per vendor and country. Margin stays empty until a verification SKU has a price."
      run={(a) => a.commercial.vendorCosts(new Date(start).toISOString(), new Date(end).toISOString())}
    >
      <div className="mb-3 grid gap-3 sm:grid-cols-2">
        <TextField label="From" type="datetime-local" value={start} onChange={setStart} />
        <TextField label="To" type="datetime-local" value={end} onChange={setEnd} />
      </div>
    </Reader>
  );
}

type OpenServiceRequest = ServiceRequest & { tenant_id: string };

/** The next moves a request may make, in the order the work goes. A closed request has none. */
export function nextMoves(status: ServiceRequest["status"]): ("IN_PROGRESS" | "ACTIVE" | "DECLINED")[] {
  if (status === "REQUESTED") return ["IN_PROGRESS", "DECLINED"];
  if (status === "IN_PROGRESS") return ["ACTIVE", "DECLINED"];
  return [];
}

const MOVE_LABEL = { IN_PROGRESS: "Start", ACTIVE: "Mark in place", DECLINED: "Decline" } as const;

/** Companies asking for dedicated isolation, sovereign service, key custody or named support. Each is delivered by people; the status and note below are what the company sees. */
export function ServiceRequestsView() {
  const list = useCellQuery([...COMMERCIAL_KEY, "service-requests"], (api) => api.commercial.serviceRequests());
  const [note, setNote] = useState("");
  const move = useCellMutation<{ id: string; status: "IN_PROGRESS" | "ACTIVE" | "DECLINED" }, unknown>((api, v) => api.commercial.moveServiceRequest(v.id, v.status, note), {
    invalidate: [COMMERCIAL_KEY],
    success: "Recorded, and the company is told",
  });
  const columns: Column<OpenServiceRequest>[] = [
    { key: "t", header: "Company", pinned: true, render: (r) => <span className="font-mono text-2xs">{r.tenant_id}</span>, sortValue: (r) => r.tenant_id },
    { key: "s", header: "Service", render: (r) => <Badge tone="info">{r.service.replace(/_/g, " ").toLowerCase()}</Badge>, sortValue: (r) => r.service },
    { key: "st", header: "Status", render: (r) => <StatusBadge state={r.status} kind="Service request" /> },
    { key: "d", header: "What they asked", render: (r) => <span className="max-w-md whitespace-pre-wrap text-sm">{r.details}</span> },
    { key: "c", header: "Contact", render: (r) => r.contact },
    { key: "w", header: "Asked", render: (r) => new Date(r.created_at).toLocaleString("en-GB"), sortValue: (r) => r.created_at },
    {
      key: "a",
      header: "Move",
      align: "end",
      render: (r) => (
        <span className="flex justify-end gap-2">
          {nextMoves(r.status).map((m) => (
            <Button key={m} size="xs" variant={m === "DECLINED" ? "secondary" : "primary"} isDisabled={move.isPending || (m === "DECLINED" && note.trim().length < 3)} onPress={() => move.mutate({ id: r.request_id, status: m })}>
              {MOVE_LABEL[m]}
            </Button>
          ))}
        </span>
      ),
    },
  ];
  return (
    <QueryView query={list}>
      {(d) => (
        <Card title="Enterprise service requests" description="Open requests, oldest first. The note below goes to the company; declining needs one." padded={false}>
          <div className="p-4">
            <TextField label="Note to the company" value={note} onChange={setNote} />
          </div>
          <DataTable<OpenServiceRequest>
            caption="Open service requests"
            columns={columns}
            rows={d.items}
            rowKey={(r) => r.request_id}
            empty={
              <EmptyState icon={Inbox} title="No open service requests">
                Companies ask from Settings, Enterprise services.
              </EmptyState>
            }
          />
        </Card>
      )}
    </QueryView>
  );
}
