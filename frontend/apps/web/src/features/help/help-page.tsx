"use client";

import { Card, KeyValues, PageHeader } from "@zoiko/ui";
import Link from "next/link";

import { useTenant } from "@/features/onboarding/shared";

const SHORTCUTS: [string, string][] = [
  ["Ctrl or ⌘ + K", "Open search and the command palette"],
  ["Esc", "Close a dialog, menu or the palette"],
  ["Tab / Shift + Tab", "Move between controls; focus is never trapped"],
];

export function HelpPage() {
  const company = useTenant();
  const tenant = company.data;
  return (
    <>
      <PageHeader title="Help & support" description="Where to look first, what to tell support, and how the console works." />
      <div className="space-y-4">
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
