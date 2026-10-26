"use client";

import type { ConsentOption, ConsentRecord, RightsRequest } from "@zoiko/api-client";
import { Badge, Banner, Button, Card, Checkbox, DataTable, DateTime, Dialog, EmptyState, PageHeader, Select, StatusBadge, Switch, type Column } from "@zoiko/ui";
import { Download, FileText, ShieldOff } from "lucide-react";
import { useState } from "react";

import { ApiErrorView, QueryView, useCellMutation, useCellQuery } from "@/lib/cell";

const KEY = ["me", "privacy"];

const PREFERENCES = [
  { id: "OPT_OUT_SALE_SHARING", label: "Do not sell or share my personal information" },
  { id: "LIMIT_SENSITIVE_PI", label: "Limit the use of my sensitive personal information" },
  { id: "DIRECT_MARKETING_OPT_OUT", label: "No direct marketing" },
  { id: "ADMT_OPT_OUT", label: "Opt out of automated decision-making where the law allows" },
];

const SELF_REQUESTS = [
  { id: "ACCESS", label: "Get a copy of my data", description: "A structured package of what we hold and why" },
  { id: "PORTABILITY", label: "Export my data to take elsewhere", description: "Machine-readable JSON" },
  { id: "RECTIFICATION", label: "Correct my data", description: "Reviewed by our privacy team" },
  { id: "ERASURE", label: "Delete my data", description: "Except what the law requires us to keep" },
  { id: "RESTRICTION", label: "Restrict processing", description: "We keep it but stop using it" },
  { id: "OBJECTION", label: "Object to processing", description: "Stop processing based on legitimate interests" },
  { id: "COMPLAINT", label: "Make a privacy complaint", description: "Tracked separately from support tickets" },
];

export function PrivacyCenter() {
  return (
    <>
      <PageHeader title="Privacy" description="Your choices, consents and data-rights requests. Opt-outs take effect at once and never need identity documents." />
      <div className="space-y-5">
        <PreferencesCard />
        <ConsentOptionsCard />
        <ConsentsCard />
        <RequestsCard />
      </div>
    </>
  );
}

function PreferencesCard() {
  const prefs = useCellQuery([...KEY, "prefs"], (api) => api.privacy.myPreferences());
  const set = useCellMutation((api, v: { type: string; value: boolean }) => api.privacy.setPreference(v.type, v.value), {
    invalidate: [[...KEY, "prefs"]],
    success: "Preference saved",
  });
  return (
    <Card title="Privacy choices" description="Browsers that send a Global Privacy Control signal are honored automatically.">
      <QueryView query={prefs}>
        {(d) => {
          const on = new Map(d.items.map((p) => [p.preference_type, p.value]));
          return (
            <div className="space-y-3">
              {PREFERENCES.map((p) => (
                <Switch key={p.id} isSelected={on.get(p.id) ?? false} isDisabled={set.isPending} onChange={(value) => set.mutate({ type: p.id, value })}>
                  {p.label}
                </Switch>
              ))}
              {set.error ? <ApiErrorView error={set.error} /> : null}
            </div>
          );
        }}
      </QueryView>
    </Card>
  );
}

/** What this person can agree to. Consent is captured against the exact notice shown, so the notice is named. */
function ConsentOptionsCard() {
  const options = useCellQuery([...KEY, "consent-options"], (api) => api.privacy.consentOptions());
  const [read, setRead] = useState<Record<string, boolean>>({});
  const give = useCellMutation(
    (api, o: ConsentOption) =>
      api.privacy.giveConsent({
        activity_code: o.activity_code,
        purpose_code: o.purpose_code,
        notice_version: o.notice_version,
        capture_method: "WEB",
        affirmative_action: `checkbox:${o.purpose_code.toLowerCase()}`,
      }),
    {
      invalidate: [[...KEY, "consent-options"], [...KEY, "consents"]],
      success: "Thank you: your consent was recorded. You can withdraw it at any time below.",
    },
  );
  return (
    <Card
      title="Optional uses of your data"
      description="You are asked only for things that need your consent. Saying no never affects the service you use."
    >
      <QueryView query={options} loadingRows={2}>
        {(d) =>
          d.items.length === 0 ? (
            <EmptyState icon={FileText} title="Nothing to agree to" compact>
              Your organization does not ask for consent to any optional processing right now.
            </EmptyState>
          ) : (
            <ul className="space-y-4">
              {d.items.map((o) => {
                const key = `${o.activity_code}:${o.purpose_code}`;
                return (
                  <li key={key} className="rounded-control border border-border p-4">
                    <div className="flex flex-wrap items-start justify-between gap-2">
                      <div>
                        <h3 className="text-sm font-semibold text-ink">{o.activity_name}</h3>
                        <p className="mt-1 text-sm text-ink-muted">{o.description}</p>
                        <p className="mt-1 text-xs text-ink-subtle">
                          Data used: {o.data_categories.join(", ") || "—"} · Notice {o.notice_version}
                          {o.notice_uri ? (
                            <>
                              {" · "}
                              <a className="underline" href={o.notice_uri} target="_blank" rel="noopener noreferrer">
                                Read the notice
                              </a>
                            </>
                          ) : null}
                        </p>
                      </div>
                      {o.consented ? <Badge tone="ok">Consent given</Badge> : null}
                    </div>
                    {o.consented ? null : (
                      <div className="mt-3 space-y-3">
                        <Checkbox isSelected={Boolean(read[key])} onChange={(v) => setRead((r) => ({ ...r, [key]: v }))}>
                          I have read notice {o.notice_version} and I agree.
                        </Checkbox>
                        <Button size="sm" isDisabled={!read[key]} pending={give.isPending} onPress={() => give.mutate(o)}>
                          Give consent
                        </Button>
                      </div>
                    )}
                  </li>
                );
              })}
            </ul>
          )
        }
      </QueryView>
      {give.error ? <ApiErrorView error={give.error} /> : null}
    </Card>
  );
}

function ConsentsCard() {
  const consents = useCellQuery([...KEY, "consents"], (api) => api.privacy.myConsents());
  const withdraw = useCellMutation((api, c: ConsentRecord) => api.privacy.withdrawConsent(c.consent_id), {
    invalidate: [[...KEY, "consents"]],
    success: "Consent withdrawn: processing that depended on it stops",
  });
  const columns: Column<ConsentRecord>[] = [
    { key: "purpose", header: "Purpose", pinned: true, render: (c) => <span className="font-mono text-xs">{c.purpose_code}</span> },
    { key: "status", header: "Status", render: (c) => <StatusBadge state={c.status} kind="Consent" /> },
    { key: "notice", header: "Notice shown", render: (c) => <span className="font-mono text-2xs">{c.notice_version}</span> },
    { key: "given", header: "Given", render: (c) => <DateTime value={c.granted_at} /> },
    {
      key: "actions",
      header: "Actions",
      align: "end",
      render: (c) =>
        c.status === "ACTIVE" ? (
          <Button size="xs" variant="secondary" pending={withdraw.isPending} onPress={() => withdraw.mutate(c)}>
            Withdraw
          </Button>
        ) : null,
    },
  ];
  return (
    <Card padded={false} title="Consents" description="Withdrawing stops only processing that relies on that consent; it never deletes the record of what you agreed to.">
      <QueryView query={consents}>
        {(d) => (
          <DataTable
            caption="My consents"
            columns={columns}
            rows={d.items}
            rowKey={(c) => c.consent_id}
            empty={<EmptyState icon={FileText} title="No consents">You have not given consent for any optional processing.</EmptyState>}
          />
        )}
      </QueryView>
      {withdraw.error ? <ApiErrorView error={withdraw.error} /> : null}
    </Card>
  );
}

function RequestsCard() {
  const requests = useCellQuery([...KEY, "requests"], (api) => api.privacy.myRequests());
  const packs = useCellQuery([...KEY, "packs"], (api) => api.privacy.packs());
  const [opening, setOpening] = useState(false);
  const [type, setType] = useState("ACCESS");
  const [pack, setPack] = useState("uk-gdpr");
  const open = useCellMutation((api) => api.privacy.openRequest({ request_type: type, jurisdiction_pack_id: pack, scope: {} }), {
    invalidate: [[...KEY, "requests"]],
    success: "Request received",
    onSuccess: () => setOpening(false),
  });
  const download = useCellMutation((api, r: RightsRequest) => api.privacy.downloadPackage(r.request_id), {
    onSuccess: (pkg) => {
      const a = document.createElement("a");
      a.href = URL.createObjectURL(new Blob([JSON.stringify(pkg, null, 2)], { type: "application/json" }));
      a.download = "my-zoiko-data.json";
      a.click();
      URL.revokeObjectURL(a.href);
    },
  });
  const columns: Column<RightsRequest>[] = [
    { key: "type", header: "Request", pinned: true, render: (r) => SELF_REQUESTS.find((s) => s.id === r.request_type)?.label ?? r.request_type },
    { key: "status", header: "Status", render: (r) => <StatusBadge state={r.status} kind="Rights request" /> },
    { key: "received", header: "Received", render: (r) => <DateTime value={r.received_at} /> },
    {
      key: "actions",
      header: "Actions",
      align: "end",
      render: (r) =>
        r.response_package_ref ? (
          <Button size="xs" variant="secondary" icon={<Download aria-hidden className="size-3.5" />} pending={download.isPending} onPress={() => download.mutate(r)}>
            Download
          </Button>
        ) : null,
    },
  ];
  return (
    <>
      <Card
        padded={false}
        title="My data-rights requests"
        description="Downloads need a fresh sign-in and expire after seven days."
        actions={
          <Button size="sm" icon={<ShieldOff aria-hidden className="size-4" />} onPress={() => setOpening(true)}>
            New request
          </Button>
        }
      >
        <QueryView query={requests}>
          {(d) => <DataTable caption="My requests" columns={columns} rows={d.items} rowKey={(r) => r.request_id} empty={<EmptyState icon={FileText} title="No requests yet" />} />}
        </QueryView>
        {download.error ? <ApiErrorView error={download.error} /> : null}
      </Card>
      <Dialog
        isOpen={opening}
        onOpenChange={(o) => !o && setOpening(false)}
        title="Make a data-rights request"
        footer={
          <>
            <Button variant="secondary" onPress={() => setOpening(false)}>
              Cancel
            </Button>
            <Button pending={open.isPending} onPress={() => open.mutate(undefined)}>
              Submit request
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          <Select label="What would you like to do?" value={type} onChange={setType} options={SELF_REQUESTS} />
          <Select
            label="Applicable law"
            value={pack}
            onChange={setPack}
            options={(packs.data?.items ?? []).map((p) => ({ id: p.pack_id, label: p.framework || p.pack_id }))}
          />
          {type === "ERASURE" ? (
            <Banner tone="warn" title="Deletion cannot be undone">
              Records we must keep by law, or that are under a legal hold, are restricted instead of deleted; we tell you which and why.
            </Banner>
          ) : null}
          {open.error ? <ApiErrorView error={open.error} /> : null}
        </div>
      </Dialog>
    </>
  );
}
