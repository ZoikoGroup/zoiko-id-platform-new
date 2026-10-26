"use client";

import { Badge, Banner, Card, DataTable, KeyValues, type Column } from "@zoiko/ui";

import { QueryView, useCellQuery } from "@/lib/cell";

type Purpose = { purpose: string; profile: string; algorithms: string[] };

export function CryptographySettings() {
  const crypto = useCellQuery(["settings", "cryptography"], (api) => api.settings.cryptography());
  const columns: Column<Purpose>[] = [
    { key: "purpose", header: "Purpose", pinned: true, render: (p) => <span className="font-mono text-xs">{p.purpose}</span>, sortValue: (p) => p.purpose },
    { key: "profile", header: "Profile", render: (p) => <span className="font-mono text-xs">{p.profile}</span> },
    {
      key: "algs",
      header: "Allowed algorithms",
      render: (p) => (
        <span className="inline-flex max-w-96 flex-wrap gap-1">
          {p.algorithms.map((a) => (
            <Badge key={a} size="xs">{a}</Badge>
          ))}
        </span>
      ),
    },
  ];
  return (
    <QueryView query={crypto}>
      {(c) => (
        <div className="space-y-4">
          <Card title="Key management" description="Read-only. Raw keys are never shown; rotation and key ceremonies are run by the platform.">
            <KeyValues
              items={[
                ["Key mode", <Badge key="m" tone="ok" size="xs">{c.key_mode.replace("_", " ").toLowerCase()}</Badge>],
                ["Key provider", c.key_provider],
                ["Residency key profile", c.residency_key_profile ?? "Not set"],
                ["Bring your own key (BYOK)", c.customer_managed_keys.byok.available ? c.customer_managed_keys.byok.state : "Not enabled"],
                ["Hold your own key (HYOK)", c.customer_managed_keys.hyok.available ? c.customer_managed_keys.hyok.state : "Not enabled"],
                ["Algorithm policy release", <code key="v" className="font-mono text-xs">{c.policy_registry_version}</code>],
              ]}
            />
          </Card>
          <Card padded={false} title="Algorithms by purpose" description="Each key purpose is separate and can only use the algorithms listed.">
            <DataTable caption="Key purposes and allowed algorithms" columns={columns} rows={c.purposes} rowKey={(p) => p.purpose} />
          </Card>
          <Card title="Published signing keys" description="Key IDs receivers can verify against. These are public identifiers, not key material.">
            <ul className="space-y-1 text-sm">
              {Object.entries(c.published_signing_key_ids).map(([name, ids]) => (
                <li key={name} className="flex flex-wrap items-center gap-2">
                  <span className="w-28 text-ink-muted">{name}</span>
                  {ids.map((id) => (
                    <code key={id} className="font-mono text-xs">{id}</code>
                  ))}
                </li>
              ))}
            </ul>
          </Card>
          <Banner tone="info" title="Customer-managed keys">
            {c.note} Bring-your-own-key and hold-your-own-key are not offered yet.
          </Banner>
        </div>
      )}
    </QueryView>
  );
}
