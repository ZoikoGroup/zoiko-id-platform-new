"use client";

import type { BillingOverview, BillingOverviewState } from "@zoiko/api-client";
import { Badge, Banner, Button, Card, EmptyState, KeyValues, RelativeTime, type Tone } from "@zoiko/ui";
import { Receipt } from "lucide-react";

import { QueryView, useCellQuery } from "@/lib/cell";

import { BILLING_KEY, money } from "./shared";

const STATE_LABEL: Record<BillingOverviewState, { label: string; tone: Tone }> = {
  FREE: { label: "Free", tone: "neutral" },
  PENDING_APPROVAL: { label: "Pending approval", tone: "info" },
  ACTIVE: { label: "Active", tone: "ok" },
  PAST_DUE: { label: "Past due", tone: "warn" },
  PILOT: { label: "Evaluation", tone: "info" },
  RESTRICTED: { label: "Restricted", tone: "danger" },
};

/** B01. The server says what state the account is in and the one thing worth doing next; this page only shows it. Nothing here is a default:
 * a free account has no invoice, no price and no invented total. */
export function OverviewSection({ goto }: { goto: (tab: string) => void }) {
  const overview = useCellQuery([...BILLING_KEY, "overview"], (api) => api.billing.overview());
  return <QueryView query={overview}>{(o) => <Overview o={o} goto={goto} />}</QueryView>;
}

function Overview({ o, goto }: { o: BillingOverview; goto: (tab: string) => void }) {
  const s = STATE_LABEL[o.state];
  const act = o.next_action;
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <Badge tone={s.tone}>{s.label}</Badge>
        <p className="text-sm text-ink">{o.message}</p>
        <span className="ms-auto text-xs text-ink-subtle">
          Data as of <RelativeTime value={o.as_of} />
        </span>
      </div>

      <Card title="Next action" description="Only the most important valid action is offered. If there is none, this says why.">
        <div className="flex flex-wrap items-center gap-4">
          <p className="min-w-64 flex-1 text-sm text-ink-muted">{act.reason}</p>
          {act.kind !== "NONE" && act.target ? (
            <Button onPress={() => goto(act.target as string)}>{act.label}</Button>
          ) : act.kind === "CONTACT_SALES" ? (
            <Banner tone="info" className="max-w-md">
              Contact your Zoiko account manager. Checkout stays closed while this is in place.
            </Banner>
          ) : (
            <span className="text-sm font-medium text-ink-muted">{act.label}</span>
          )}
        </div>
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Current plan">
          <KeyValues
            columns={2}
            items={[
              ["Plan", o.plan.code ?? "Free (no paid plan)"],
              ["Source", o.plan.source === "ORDER" ? "Signed order" : o.plan.source === "OPERATOR" ? "Set by Zoiko" : "—"],
              ["Term", o.plan.term_months ? `${o.plan.term_months} months` : "—"],
              ["Contract reference", o.plan.contract_ref ? <span className="font-mono text-xs">{o.plan.contract_ref}</span> : "—"],
              ["Effective from", o.plan.effective_from ? new Date(o.plan.effective_from).toLocaleDateString("en-GB") : "—"],
            ]}
          />
          {o.pilot ? (
            <p className="mt-3 text-sm">
              Evaluation ends {o.pilot.ends_at ? new Date(o.pilot.ends_at).toLocaleDateString("en-GB") : "on its approved date"}. <strong>No automatic conversion.</strong>
            </p>
          ) : null}
        </Card>
        <Card title="Issuer and legal payer" description="The legal entities on your invoices, not the brand.">
          <KeyValues
            columns={2}
            items={[
              ["Legal payer", o.payer.legal_name ?? "Not set up"],
              ["Country", o.payer.country ?? "—"],
              ["Currency", o.payer.currency ?? "—"],
              ["Issued by", o.payer.issuer ?? "No invoice issued yet"],
            ]}
          />
        </Card>
      </div>

      {o.restricted.length ? (
        <Banner tone="warn" title="Restricted right now">
          {o.restricted.join(", ")}. Sign-in and revocation are never restricted for billing.
        </Banner>
      ) : null}

      <Card title="Recent billing activity" description="Real invoices only. Nothing appears here until one is issued." padded={false}>
        {o.recent_activity.length ? (
          <ul className="divide-y divide-border">
            {o.recent_activity.map((a) => (
              <li key={a.invoice_id} className="flex flex-wrap items-center gap-3 px-5 py-3 text-sm">
                <span className="font-mono text-xs">{a.ref ?? "—"}</span>
                <span className="tabular">{money(a.amount, a.currency)}</span>
                <Badge tone={a.status === "PAID" ? "ok" : "warn"}>{a.status === "PAID" ? "Settled" : "Open"}</Badge>
                <span className="ms-auto text-xs text-ink-subtle">{a.at ? new Date(a.at).toLocaleDateString("en-GB") : ""}</span>
              </li>
            ))}
          </ul>
        ) : (
          <EmptyState title="No invoices yet" icon={Receipt}>
            {o.state === "FREE" ? "A free account has no invoices." : "Invoices appear here once a month has been closed and issued."}
          </EmptyState>
        )}
        {o.recent_activity.length ? (
          <div className="border-t border-border px-5 py-3">
            <Button variant="secondary" onPress={() => goto("invoices")}>
              All invoices
            </Button>
          </div>
        ) : null}
      </Card>
    </div>
  );
}
