"use client";

import { Banner, Card, KeyValues, PageHeader } from "@zoiko/ui";
import Link from "next/link";
import { useSearchParams } from "next/navigation";

import { useTenant } from "@/features/onboarding/shared";

const SHORTCUTS: [string, string][] = [
  ["Ctrl or ⌘ + K", "Open search and the command palette"],
  ["Esc", "Close a dialog, menu or the palette"],
  ["Tab / Shift + Tab", "Move between controls; focus is never trapped"],
];

/** The billing questions the invoice and billing-profile pages link here with (`?topic=...`). */
const BILLING_TOPICS: Record<string, { title: string; text: (invoice: string | null) => string }> = {
  "billing-dispute": {
    title: "Question an invoice",
    text: (invoice) =>
      `Contact your Zoiko account manager and quote ${invoice ? `invoice ${invoice}` : "the invoice number"}. Say what looks wrong: an amount, a date, a payment you made or a credit you expected. An issued invoice is never edited: a correction is a linked credit note, which Zoiko Finance approves.`,
  },
  "billing-payer-change": {
    title: "Change the legal payer or tax country",
    text: () =>
      "A change of legal payer or tax country is a reviewed request, not an edit. Contact your Zoiko account manager with the new legal name, country and tax number. The owner of your company confirms it, and Zoiko Finance reviews it before any invoice changes.",
  },
};

function BillingTopic() {
  const sp = useSearchParams();
  const topic = BILLING_TOPICS[sp.get("topic") ?? ""];
  if (!topic) return null;
  return (
    <Banner tone="info" title={topic.title}>
      {topic.text(sp.get("invoice"))}
    </Banner>
  );
}

export function HelpPage() {
  const company = useTenant();
  const tenant = company.data;
  return (
    <>
      <PageHeader title="Help & support" description="Where to look first, what to tell support, and how the console works." />
      <div className="space-y-4">
        <BillingTopic />
        <Card title="Quick answers">
          <ul className="list-disc space-y-2 pl-5 text-sm text-ink-muted">
            <li>
              <strong>Something needs attention?</strong> Open <Link className="font-medium text-brand-600 hover:underline" href="/alerts">Alerts &amp; tasks</Link>. Each alert links to the object and a safe next step.
            </li>
            <li>
              <strong>An action was refused?</strong> The message names the reason: missing authority, a fresh passkey check (step-up), or a second administrator&apos;s approval. Nothing is hidden because of a role label; the platform decides each action.
            </li>
            <li>
              <strong>Not receiving webhooks?</strong> Check the endpoint is Active and read the delivery log under{" "}
              <Link className="font-medium text-brand-600 hover:underline" href="/admin/developers">Developers</Link>.
            </li>
            <li>
              <strong>Need Zoiko iD staff to look at your data?</strong> Staff have no standing access. Grant time-limited access under{" "}
              <Link className="font-medium text-brand-600 hover:underline" href="/admin/operator-access">Operator access</Link>.
            </li>
          </ul>
        </Card>
        <Card title="When you contact support" description="Give support these identifiers; never send passwords, passkeys, recovery codes or secrets.">
          <KeyValues
            items={[
              ["Company", tenant?.display_name ?? "—"],
              ["Company ID", <code key="t" className="font-mono text-xs break-all">{tenant?.tenant_id ?? "—"}</code>],
              ["Home cell", <code key="c" className="font-mono text-xs">{tenant?.home_cell_id ?? "—"}</code>],
              ["Support reference", "Shown in any error message as a correlation ID; copy it exactly."],
            ]}
          />
        </Card>
        <Card title="Keyboard">
          <KeyValues items={SHORTCUTS.map(([k, v]) => [<kbd key={k} className="rounded border border-line px-1.5 py-0.5 font-mono text-xs">{k}</kbd>, v])} />
        </Card>
      </div>
    </>
  );
}
