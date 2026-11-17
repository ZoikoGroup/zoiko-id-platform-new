"use client";

import type { ConsentOption, ConsentRecord, MyReview, RightsRequest } from "@zoiko/api-client";
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
  { id: "HUMAN_REVIEW", label: "Ask a person to review a decision", description: "For example a verification or biometric check that went against you" },
  { id: "COMPLAINT", label: "Make a privacy complaint", description: "Tracked separately from support tickets" },
];

/** What a request's status means for the person (WF-04 B06). A limited answer always says which records were kept and why. */
export const REQUEST_STATUS_TEXT: Record<string, string> = {
  RECEIVED: "Received. We confirm it is you before we act.",
  VERIFYING: "We are confirming it is you.",
  SCOPING: "We are finding where your data is held.",
  FULFILLING: "In progress, including with our providers.",
  REVIEW: "A person is reviewing it.",
  COMPLETED: "Done.",
  PARTIALLY_DENIED: "Done, but some records are kept because the law requires it or a legal hold applies. The answer names them and why.",
  DENIED: "Refused. The answer says why and how to challenge it.",
  WITHDRAWN: "You withdrew this request.",
};

export function PrivacyCenter() {
  return (
    <>
      <PageHeader title="Privacy" description="Your choices, consents and data-rights requests. Opt-outs take effect at once and never need identity documents." />
      <div className="space-y-5">
        <PreferencesCard />
        <ConsentOptionsCard />
        <ConsentsCard />
        <RequestsCard />
        <MyReviewsCard />
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
  const [confirming, setConfirming] = useState<ConsentRecord | null>(null);
  const withdraw = useCellMutation((api, c: ConsentRecord) => api.privacy.withdrawConsent(c.consent_id), {
    invalidate: [[...KEY, "consents"]],
    success: "Consent withdrawn: processing that depended on it stops",
    onSuccess: () => setConfirming(null),
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
          <Button size="xs" variant="secondary" onPress={() => setConfirming(c)} aria-label={`Withdraw consent for ${c.purpose_code}`}>
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
      <Dialog
        isOpen={confirming !== null}
        onOpenChange={(o) => !o && setConfirming(null)}
        title="Withdraw this consent?"
        footer={
          <>
            <Button variant="secondary" onPress={() => setConfirming(null)}>
              Keep it
            </Button>
            <Button variant="danger" pending={withdraw.isPending} onPress={() => confirming && withdraw.mutate(confirming)}>
              Withdraw
            </Button>
          </>
        }
      >
        <ul className="list-disc space-y-1.5 pl-5 text-sm" aria-label="What withdrawing does">
          <li>Processing that relies on this consent stops from now, including any biometric check in a session that is still open.</li>
          <li>Decisions already made are not reversed by withdrawing. To challenge one, ask a person to review it.</li>
          <li>Withdrawing does not delete data. To delete, make a deletion request; records the law requires us to keep are restricted instead.</li>
          <li>Where a check needed this consent, you can still verify another way, such as an assisted review.</li>
        </ul>
      </Dialog>
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
    { key: "meaning", header: "What it means", render: (r) => <span className="text-xs">{REQUEST_STATUS_TEXT[r.status] ?? ""}</span> },
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

/** What each review state means for the person who asked (WF-04 B08; CR-110). */
export const REVIEW_STATE_TEXT: Record<string, string> = {
  OPEN: "Waiting for a person other than you to review it.",
  AWAITING_CHECKER: "A reviewer proposed changing the decision; a second person is checking it.",
  UPHELD: "Reviewed: the decision stands. You can make a complaint if you disagree.",
  OVERTURNED: "Reviewed: the decision was reversed. Anything that relied on it was withdrawn; you may be asked to verify again.",
  CORRECTED: "Reviewed: the decision was corrected.",
};

function MyReviewsCard() {
  const q = useCellQuery([...KEY, "reviews"], (api) => api.privacy.v2.myReviews());
  const columns: Column<MyReview>[] = [
    { key: "asked", header: "Asked", pinned: true, render: (r) => <DateTime value={r.created_at} /> },
    { key: "kind", header: "Kind", render: (r) => (r.kind === "APPEAL" ? "Appeal" : "Human review") },
    { key: "state", header: "Status", render: (r) => <StatusBadge state={r.state} kind="Review" /> },
    { key: "meaning", header: "What it means", render: (r) => <span className="text-xs">{REVIEW_STATE_TEXT[r.state] ?? ""}</span> },
    { key: "due", header: "Due by", render: (r) => <DateTime value={r.due_at} style="date" /> },
  ];
  return (
    <Card padded={false} title="My appeals and reviews" description="Decisions you asked a person to look at again. Asking never counts against you.">
      <QueryView query={q}>
        {(d) => <DataTable caption="My reviews" columns={columns} rows={d.items} rowKey={(r) => r.review_id} empty={<EmptyState icon={FileText} title="No appeals or reviews" />} />}
      </QueryView>
    </Card>
  );
}
