"use client";

import type { Eligibility, FinanceAppointment, FinanceKind, FinanceOverview, FinancePolicy, FinanceRequest } from "@zoiko/api-client";
import { Badge, Banner, Button, Card, DataTable, Dialog, EmptyState, KeyValues, Select, StatusBadge, TextField, type Column } from "@zoiko/ui";
import { Banknote, CheckCircle2, ShieldAlert, UserCog, XCircle } from "lucide-react";
import { useState, type FormEvent } from "react";

import { ApiErrorView, QueryView, newIdempotencyKey, useCellMutation, useCellQuery } from "@/lib/op";

import { CapabilityHint } from "../recovery-shared";
import {
  CATEGORIES,
  CLASSIFICATION_LABEL,
  DISPOSITIONS,
  GATE_STATE_LABEL,
  KIND_LABEL,
  STATE_LABEL,
  daysLeft,
  emptyForm,
  executionKey,
  offeredActions,
  openBody,
  roleLabel,
  rolesStillNeeded,
  shortHash,
  validateOpen,
  type OpenForm,
} from "./finance-logic";

export const FINANCE_KEY = ["operator", "finance"];
const CAP = "commercial.finance.manage";

/** F01. Queue measurements as of a time, never revenue or cash; what waits on this person; and what stops money moving. */
export function FinanceOverviewView() {
  const overview = useCellQuery([...FINANCE_KEY, "overview"], (api) => api.finance.overview());
  return (
    <QueryView query={overview} loadingRows={3}>
      {(o) => <Overview o={o} />}
    </QueryView>
  );
}

function Overview({ o }: { o: FinanceOverview }) {
  const c = o.counts;
  const tiles: [string, number][] = [
    ["Waiting for approval", c.pending_approval],
    ["Approved, not yet run", c.approved_unexecuted],
    ["Application failed", c.failed_application],
    ["Expiring in three days", c.expiring_soon],
    ["Payment events open", c.payment_events_open],
  ];
  return (
    <div className="space-y-4">
      <div className="grid gap-3 sm:grid-cols-3 lg:grid-cols-5">
        {tiles.map(([label, n]) => (
          <Card key={label} title={label}>
            <p className="tabular text-2xl font-semibold text-ink">{n}</p>
          </Card>
        ))}
      </div>
      <p className="text-xs text-ink-subtle">
        As of {new Date(o.as_of).toLocaleString("en-GB")}. {o.definitions}
      </p>
      <Card title="Waiting on you" description="Requests that still need a role you hold, that you did not raise and have not signed.">
        {o.mine.length ? (
          <ul className="list-disc space-y-1 pl-5 text-sm">
            {o.mine.map((id) => (
              <li key={id} className="font-mono text-xs">{id}</li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-ink-muted">Nothing waits on you.</p>
        )}
      </Card>
      {o.exceptions.length ? (
        <div className="space-y-2" aria-label="Exceptions">
          {o.exceptions.map((x) => (
            <Banner key={x.key} tone={x.severity === "danger" ? "danger" : "warn"} icon={ShieldAlert}>
              {x.text}
            </Banner>
          ))}
        </div>
      ) : (
        <Banner tone="ok">No exceptions are stopping money from moving.</Banner>
      )}
    </div>
  );
}

/** F02 and F03. Open a credit note or a pilot credit; each request is frozen, hashed, signed by distinct people and run once. */
export function FinanceRequestsView() {
  const policy = useCellQuery([...FINANCE_KEY, "policy"], (api) => api.finance.policy());
  const [state, setState] = useState("");
  const list = useCellQuery([...FINANCE_KEY, "requests", state], (api) => api.finance.requests(state || undefined));
  const [open, setOpen] = useState<string | null>(null);
  const columns: Column<FinanceRequest>[] = [
    { key: "k", header: "Kind", pinned: true, render: (r) => KIND_LABEL[r.kind], sortValue: (r) => r.kind },
    { key: "a", header: "Amount", render: (r) => `${r.amount} ${r.currency}`, sortValue: (r) => r.usd_minor },
    { key: "t", header: "Organization", render: (r) => <span className="font-mono text-2xs">{r.tenant_id}</span> },
    { key: "s", header: "State", render: (r) => <Badge tone={STATE_LABEL[r.state].tone}>{STATE_LABEL[r.state].label}</Badge>, sortValue: (r) => r.state },
    { key: "n", header: "Signed", render: (r) => `${r.approvals.filter((a) => a.decision === "APPROVE").length} of ${r.required_roles.length}` },
    { key: "w", header: "Opened", render: (r) => new Date(r.created_at).toLocaleDateString("en-GB"), sortValue: (r) => r.created_at },
  ];
  return (
    <div className="space-y-4">
      <OpenRequest maxPilotDays={policy.data?.pilot_credit.max_days ?? null} />
      <Card
        title="Credit notes and pilot credits"
        description="Select a request to read exactly what is being approved."
        padded={false}
        actions={
          <Select
            label="State"
            hideLabel
            options={[{ id: "", label: "All states" }, ...Object.entries(STATE_LABEL).map(([id, s]) => ({ id, label: s.label }))]}
            value={state}
            onChange={setState}
            className="w-56"
          />
        }
      >
        <QueryView query={list}>
          {(d) => (
            <DataTable<FinanceRequest>
              caption="Finance requests"
              columns={columns}
              rows={d.items}
              rowKey={(r) => r.request_id}
              onRowAction={(r) => setOpen(r.request_id)}
              empty={<EmptyState title="No requests" icon={Banknote}>Open one above.</EmptyState>}
            />
          )}
        </QueryView>
      </Card>
      {open ? <RequestDetail id={open} onClose={() => setOpen(null)} /> : null}
    </div>
  );
}

function OpenRequest({ maxPilotDays }: { maxPilotDays: number | null }) {
  const [f, setF] = useState<OpenForm>(emptyForm());
  const [shown, setShown] = useState(false);
  const [now] = useState(() => Date.now());
  const errors = validateOpen(f, now, maxPilotDays);
  const err = (k: keyof OpenForm) => (shown ? errors[k] : undefined);
  const set = <K extends keyof OpenForm>(k: K, v: OpenForm[K]) => setF((c) => ({ ...c, [k]: v }));
  const open = useCellMutation((api, form: OpenForm) => api.finance.open(openBody(form)), {
    invalidate: [FINANCE_KEY],
    success: "Request opened. The approvers it names can now sign it.",
    onSuccess: () => {
      setF(emptyForm(f.kind));
      setShown(false);
    },
  });
  const credit = f.kind === "CREDIT_NOTE";
  const submit = (e: FormEvent) => {
    e.preventDefault();
    setShown(true);
    if (Object.keys(errors).length === 0) open.mutate(f);
  };
  return (
    <Card title="Open a request" description="The amount decides the tier and who must sign. Credits for the same organization in the policy window add up.">
      <form onSubmit={submit} noValidate className="grid gap-3 sm:grid-cols-2" aria-label="Open a finance request">
        <Select
          label="Kind"
          options={(Object.keys(KIND_LABEL) as FinanceKind[]).map((id) => ({ id, label: KIND_LABEL[id] }))}
          value={f.kind}
          onChange={(v) => setF({ ...emptyForm(v as FinanceKind), tenant: f.tenant, amount: f.amount, currency: f.currency, reason: f.reason })}
        />
        <Select label="Category" options={CATEGORIES[f.kind]} value={f.category} onChange={(v) => set("category", v as OpenForm["category"])} />
        <TextField label="Organization tenant ID" value={f.tenant} onChange={(v) => set("tenant", v)} errorMessage={err("tenant")} mono isRequired />
        {credit ? <TextField label="Invoice ID" value={f.invoice} onChange={(v) => set("invoice", v)} errorMessage={err("invoice")} mono isRequired /> : null}
        <TextField label="Amount" value={f.amount} onChange={(v) => set("amount", v)} errorMessage={err("amount")} inputMode="decimal" isRequired />
        <TextField label="Currency" value={f.currency} onChange={(v) => set("currency", v.toUpperCase().slice(0, 3))} errorMessage={err("currency")} isRequired />
        {credit ? (
          <>
            <TextField
              label="Evidence"
              value={f.evidence}
              onChange={(v) => set("evidence", v)}
              errorMessage={err("evidence")}
              description={f.category === "CORRECTION" ? "Required: the source records that show the error." : "Optional for goodwill."}
              isRequired={f.category === "CORRECTION"}
            />
            <Select
              label="If the invoice is already paid"
              options={DISPOSITIONS}
              value={f.disposition}
              onChange={(v) => set("disposition", v)}
              description="A refund is a separate decision after this request is applied."
            />
          </>
        ) : (
          <>
            <TextField label="Approved pilot program" value={f.program} onChange={(v) => set("program", v)} errorMessage={err("program")} isRequired />
            <TextField label="Sponsor" value={f.sponsor} onChange={(v) => set("sponsor", v)} errorMessage={err("sponsor")} isRequired />
            <TextField
              label="Credit ends"
              type="datetime-local"
              value={f.endsAt}
              onChange={(v) => set("endsAt", v)}
              errorMessage={err("endsAt")}
              description={maxPilotDays ? `At most ${maxPilotDays} days from now.` : undefined}
              isRequired
            />
          </>
        )}
        <TextField label="Reason" value={f.reason} onChange={(v) => set("reason", v)} errorMessage={err("reason")} multiline rows={2} className="sm:col-span-2" isRequired />
        <div className="sm:col-span-2">
          <Button type="submit" isDisabled={open.isPending}>
            {open.isPending ? "Opening…" : "Open request"}
          </Button>
        </div>
      </form>
      {open.isError ? (
        <div className="mt-3 space-y-3">
          <CapabilityHint error={open.error} capability={CAP} />
          <ApiErrorView error={open.error} title="The request was not opened" onRetry={() => open.reset()} />
        </div>
      ) : null}
    </Card>
  );
}

/** F03. Re-reads the request, shows the frozen facts and the hash that is signed, and offers only what is still open. */
function RequestDetail({ id, onClose }: { id: string; onClose: () => void }) {
  const live = useCellQuery([...FINANCE_KEY, "request", id], (api) => api.finance.request(id));
  const [now] = useState(() => Date.now());
  const [role, setRole] = useState("");
  const [reason, setReason] = useState("");
  const [attempt, setAttempt] = useState(() => newIdempotencyKey());

  const decide = useCellMutation(
    (api, v: { r: FinanceRequest; decision: "APPROVE" | "REJECT" }) =>
      api.finance.decide(v.r.request_id, { role: role || rolesStillNeeded(v.r)[0]!, decision: v.decision, snapshot_hash: v.r.snapshot_hash, reason: reason.trim() || null }),
    { invalidate: [FINANCE_KEY], success: (x) => (x.state === "REJECTED" ? "Rejected" : x.state === "APPROVED" ? "Fully approved. A finance executor can run it." : "Your signature is recorded.") },
  );
  const run = useCellMutation((api, r: FinanceRequest) => api.finance.execute(r.request_id, executionKey(r.request_id, attempt)), {
    invalidate: [FINANCE_KEY],
    success: (x) => (x.state === "APPLIED" ? "Applied" : "Not applied. See the result."),
    onSuccess: () => setAttempt(newIdempotencyKey()),
  });
  const cancel = useCellMutation((api, r: FinanceRequest) => api.finance.cancel(r.request_id), { invalidate: [FINANCE_KEY], success: "Request withdrawn", onSuccess: onClose });

  return (
    <Dialog isOpen onOpenChange={(o) => !o && onClose()} title="Finance request" size="lg">
      <QueryView query={live}>
        {(r) => {
          const need = rolesStillNeeded(r);
          const actions = offeredActions(r, now);
          const chosen = role || need[0] || "";
          const expiry = daysLeft(r.state === "APPROVED" ? r.approval_expires_at : r.expires_at, now);
          return (
            <div className="space-y-4">
              <KeyValues
                columns={2}
                items={[
                  ["Kind", KIND_LABEL[r.kind]],
                  ["State", <Badge key="s" tone={STATE_LABEL[r.state].tone}>{STATE_LABEL[r.state].label}</Badge>],
                  ["Amount", `${r.amount} ${r.currency}`],
                  ["Exposure counted (USD)", (r.exposure_usd_minor / 100).toLocaleString("en-US", { minimumFractionDigits: 2 })],
                  ["Category", r.category.toLowerCase()],
                  ["Organization", <span key="t" className="font-mono text-xs">{r.tenant_id}</span>],
                  ["Invoice", r.invoice_id ? <span key="i" className="font-mono text-xs">{r.invoice_id}</span> : "—"],
                  ["Policy version", r.policy_version],
                  ["Needs", r.required_roles.map(roleLabel).join(" and ")],
                  ["Time left", expiry === null ? "—" : `${expiry} day${expiry === 1 ? "" : "s"}`],
                ]}
              />
              <p className="text-sm text-ink">{r.reason}</p>
              <p className="break-all font-mono text-2xs text-ink-subtle">
                You sign exactly this record, SHA-256 {r.snapshot_hash}. If it changes, earlier signatures stop counting.
              </p>
              {r.approvals.length ? (
                <ul className="space-y-1 text-sm" aria-label="Signatures">
                  {r.approvals.map((a) => (
                    <li key={`${a.role}-${a.principal_id}`} className="flex items-center gap-2">
                      {a.decision === "APPROVE" ? <CheckCircle2 aria-hidden className="size-4 text-ok-fg" /> : <XCircle aria-hidden className="size-4 text-danger-fg" />}
                      {a.decision === "APPROVE" ? "Approved" : "Rejected"} as {roleLabel(a.role)} · <span className="font-mono text-xs">{a.principal_id.slice(0, 8)}</span> ·{" "}
                      {new Date(a.decided_at).toLocaleString("en-GB")}
                    </li>
                  ))}
                </ul>
              ) : null}
              {r.result ? <pre className="max-h-40 overflow-auto rounded-md bg-sunken p-3 font-mono text-xs">{JSON.stringify(r.result, null, 2)}</pre> : null}
              {actions.includes("sign") ? (
                <div className="grid gap-3 sm:grid-cols-2">
                  <Select label="Sign as" options={need.map((x) => ({ id: x, label: roleLabel(x) }))} value={chosen} onChange={setRole} description="You must hold this role, a different person from the requester and from the other signers." />
                  <TextField label="Reason (required to reject)" value={reason} onChange={setReason} />
                  <div className="flex gap-2 sm:col-span-2">
                    <Button isDisabled={decide.isPending} onPress={() => decide.mutate({ r, decision: "APPROVE" })}>
                      Approve ({shortHash(r.snapshot_hash)})
                    </Button>
                    <Button variant="secondary" isDisabled={decide.isPending || !reason.trim()} onPress={() => decide.mutate({ r, decision: "REJECT" })}>
                      Reject
                    </Button>
                  </div>
                </div>
              ) : null}
              {actions.includes("execute") ? (
                <div className="flex flex-wrap items-center gap-3">
                  <Button isDisabled={run.isPending} onPress={() => run.mutate(r)}>
                    {r.state === "APPLICATION_FAILED" ? "Run again" : "Run it"}
                  </Button>
                  <span className="text-xs text-ink-subtle">Only a finance executor who did not sign. The signers&apos; roles and the policy are checked again at this moment.</span>
                </div>
              ) : null}
              {actions.includes("cancel") ? (
                <Button variant="secondary" isDisabled={cancel.isPending} onPress={() => cancel.mutate(r)}>
                  Withdraw request
                </Button>
              ) : null}
              {[decide, run, cancel].map((m, i) =>
                m.isError ? (
                  <div key={i} className="space-y-2">
                    <CapabilityHint error={m.error} capability={CAP} />
                    <ApiErrorView error={m.error} title="That was not recorded" onRetry={() => m.reset()} />
                  </div>
                ) : null,
              )}
            </div>
          );
        }}
      </QueryView>
    </Dialog>
  );
}

/** F04. Who holds which Zoiko internal role, and whether the signed policy is in force. */
export function FinanceAuthorityView() {
  return (
    <div className="space-y-4">
      <PolicyCard />
      <Appointments />
    </div>
  );
}

function PolicyCard() {
  const policy = useCellQuery([...FINANCE_KEY, "policy"], (api) => api.finance.policy());
  const sign = useCellMutation((api, hash: string) => api.finance.signPolicy(hash), { invalidate: [FINANCE_KEY], success: "Policy signed" });
  return (
    <QueryView query={policy}>
      {(p) => (
        <Card title="Finance policy" description="Tiers, approvers and windows. In a deployed environment nothing counts until the Founder and the CFO have signed this exact file.">
          <div className="space-y-3">
            <Banner tone={p.effective ? "ok" : p.locked ? "danger" : "warn"} title={p.effective ? "In force" : p.locked ? "Locked: not signed" : "Not signed (this environment does not require it)"}>
              {p.effective ? "Signed by every role the policy names." : p.awaiting.length ? `Waiting for: ${p.awaiting.map(roleLabel).join(", ")}.` : ""}
            </Banner>
            <KeyValues
              columns={2}
              items={[
                ["Version", p.version],
                ["Environment", p.environment],
                ["Unit", p.unit],
                ["Request valid for", `${p.request_valid_days} days`],
                ["Approval valid for", `${p.approval_valid_days} days`],
                ["Credit note window", `${p.credit_note.window_days} days`],
                ["Pilot credit window", `${p.pilot_credit.window_days} days, at most ${p.pilot_credit.max_days} days long`],
                ["May raise a request", p.request_roles.map(roleLabel).join(", ")],
                ["May run one", p.execute_roles.map(roleLabel).join(", ")],
              ]}
            />
            <PolicyTiers p={p} />
            <p className="break-all font-mono text-2xs text-ink-subtle">Content hash {p.content_hash}</p>
            {p.signatures.length ? (
              <ul className="text-sm">
                {p.signatures.map((s) => (
                  <li key={s.role}>
                    Signed as {roleLabel(s.role)} on {new Date(s.signed_at).toLocaleDateString("en-GB")}
                  </li>
                ))}
              </ul>
            ) : null}
            {!p.effective ? (
              <Button isDisabled={sign.isPending} onPress={() => sign.mutate(p.content_hash)}>
                Sign this policy
              </Button>
            ) : null}
            {sign.isError ? (
              <>
                <CapabilityHint error={sign.error} capability={CAP} />
                <ApiErrorView error={sign.error} title="The policy was not signed" onRetry={() => sign.reset()} />
              </>
            ) : null}
          </div>
        </Card>
      )}
    </QueryView>
  );
}

function PolicyTiers({ p }: { p: FinancePolicy }) {
  const rows = [
    ...p.credit_note.tiers.map((t) => ({ kind: "Credit note", ...t })),
    ...p.pilot_credit.tiers.map((t) => ({ kind: "Pilot credit", ...t })),
  ];
  return (
    <table className="w-full text-sm">
      <caption className="sr-only">Approval tiers by exposure in USD</caption>
      <thead>
        <tr className="border-b border-border text-left text-xs uppercase text-ink-subtle">
          <th scope="col" className="py-2">Kind</th>
          <th scope="col" className="py-2">Up to (USD)</th>
          <th scope="col" className="py-2">Signed by</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((t, i) => (
          <tr key={i} className="border-b border-border last:border-0">
            <th scope="row" className="py-2 text-left font-normal">{t.kind}</th>
            <td className="tabular py-2">{t.max_usd ?? "No upper limit"}</td>
            <td className="py-2">{t.roles.map(roleLabel).join(" and ")}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function Appointments() {
  const list = useCellQuery([...FINANCE_KEY, "appointments"], (api) => api.finance.appointments());
  const [who, setWho] = useState("");
  const [role, setRole] = useState("");
  const [until, setUntil] = useState("");
  const appoint = useCellMutation(
    (api, v: { principal_id: string; role: string; until: string }) =>
      api.finance.appoint({ principal_id: v.principal_id, role: v.role, effective_to: v.until ? new Date(`${v.until}T23:59:59`).toISOString() : null }),
    { invalidate: [FINANCE_KEY], success: "Appointed", onSuccess: () => setWho("") },
  );
  const end = useCellMutation((api, id: string) => api.finance.endAppointment(id), { invalidate: [FINANCE_KEY], success: "Appointment ended" });
  const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(who.trim());
  const columns: Column<FinanceAppointment>[] = [
    { key: "r", header: "Role", pinned: true, render: (a) => roleLabel(a.role), sortValue: (a) => a.role },
    { key: "p", header: "Person", render: (a) => <span className="font-mono text-xs">{a.principal_id}</span> },
    { key: "f", header: "From", render: (a) => a.effective_from.slice(0, 10), sortValue: (a) => a.effective_from },
    { key: "t", header: "Until", render: (a) => (a.effective_to ? a.effective_to.slice(0, 10) : "No end date") },
    { key: "s", header: "State", render: (a) => <StatusBadge state={a.revoked_at ? "REVOKED" : "ACTIVE"} kind="Appointment" /> },
    {
      key: "x",
      header: "",
      render: (a) =>
        a.revoked_at ? null : (
          <Button size="xs" variant="secondary" isDisabled={end.isPending} onPress={() => end.mutate(a.appointment_id)}>
            End
          </Button>
        ),
    },
  ];
  return (
    <>
      <Card title="Appoint to a finance role" description="Nobody appoints themselves, and one person cannot fill two slots of the same approval. Customer-side authority is never given here.">
        <QueryView query={list}>
          {(d) => (
            <form
              className="flex flex-wrap items-end gap-3"
              onSubmit={(e: FormEvent) => {
                e.preventDefault();
                if (uuid && role) appoint.mutate({ principal_id: who.trim().toLowerCase(), role, until });
              }}
            >
              <TextField label="Operator principal ID" value={who} onChange={setWho} mono className="min-w-80" />
              <Select label="Role" options={d.roles.map((r) => ({ id: r, label: roleLabel(r) }))} value={role} onChange={setRole} className="min-w-56" />
              <TextField label="Until (optional)" type="date" value={until} onChange={setUntil} className="w-48" />
              <Button type="submit" isDisabled={!uuid || !role || appoint.isPending} icon={<UserCog aria-hidden className="size-4" />}>
                Appoint
              </Button>
            </form>
          )}
        </QueryView>
        {appoint.isError ? <ApiErrorView error={appoint.error} title="The appointment was not made" onRetry={() => appoint.reset()} /> : null}
      </Card>
      <QueryView query={list}>
        {(d) => (
          <Card title="Appointments" padded={false}>
            <DataTable<FinanceAppointment>
              caption="Finance role appointments"
              columns={columns}
              rows={d.items}
              rowKey={(a) => a.appointment_id}
              empty={<EmptyState title="No one is appointed" icon={UserCog}>Nobody can approve a credit until roles are appointed.</EmptyState>}
            />
            {end.isError ? <ApiErrorView error={end.error} title="The appointment was not ended" onRetry={() => end.reset()} /> : null}
          </Card>
        )}
      </QueryView>
    </>
  );
}

/** O01. Why an organization can or cannot be activated for paid use. Reading it never activates anything. */
export function EligibilityView() {
  const [tenant, setTenant] = useState("");
  const [asked, setAsked] = useState<string | null>(null);
  const valid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(tenant.trim());
  return (
    <div className="space-y-4">
      <Card title="Paid activation eligibility" description="Enter an organization to see the gates that decide whether it can start paid use.">
        <form
          className="flex flex-wrap items-end gap-3"
          onSubmit={(e: FormEvent) => {
            e.preventDefault();
            if (valid) setAsked(tenant.trim().toLowerCase());
          }}
        >
          <TextField label="Organization tenant ID" value={tenant} onChange={setTenant} mono className="min-w-80" />
          <Button type="submit" isDisabled={!valid}>
            Check
          </Button>
        </form>
      </Card>
      {asked ? <EligibilityResult tenantId={asked} /> : null}
      <ReviewCommitment />
    </div>
  );
}

function EligibilityResult({ tenantId }: { tenantId: string }) {
  const q = useCellQuery([...FINANCE_KEY, "eligibility", tenantId], (api) => api.finance.eligibility(tenantId));
  return (
    <QueryView query={q}>
      {(e) => <EligibilityCard e={e} />}
    </QueryView>
  );
}

function EligibilityCard({ e }: { e: Eligibility }) {
  const s = GATE_STATE_LABEL[e.state];
  return (
    <Card title="Result">
      <div className="space-y-3">
        <div className="flex flex-wrap items-center gap-3">
          <Badge tone={s.tone}>{s.label}</Badge>
          <span className="text-sm">Classified as {CLASSIFICATION_LABEL[e.classification]}</span>
          {e.commitment_state ? <span className="text-sm text-ink-muted">Commitment: {e.commitment_state.toLowerCase().replace(/_/g, " ")}</span> : null}
        </div>
        <ul className="space-y-2" aria-label="Gates">
          {e.gates.map((g) => (
            <li key={g.key} className="flex items-start gap-2 text-sm">
              {g.passed ? <CheckCircle2 aria-hidden className="mt-0.5 size-4 shrink-0 text-ok-fg" /> : <XCircle aria-hidden className="mt-0.5 size-4 shrink-0 text-danger-fg" />}
              <span>
                <span className="font-medium">{g.label}</span> <span className="text-ink-muted">{g.detail}</span>
                {!g.passed && g.remedy ? <span className="block text-xs text-ink-subtle">To fix: {g.remedy}</span> : null}
              </span>
            </li>
          ))}
        </ul>
        {e.schedule ? (
          <p className="text-sm text-ink-muted">
            Order <span className="font-mono text-xs">{e.schedule.order_id}</span> starts {new Date(e.schedule.effective_from).toLocaleDateString("en-GB")}
            {e.schedule.pilot_ends_at ? `, evaluation ends ${new Date(e.schedule.pilot_ends_at).toLocaleDateString("en-GB")}` : ""}.
          </p>
        ) : null}
        <p className="text-xs text-ink-subtle">{e.note}</p>
      </div>
    </Card>
  );
}

/** A Controller or CFO marks a customer's non-standard terms reviewed; only then do the customer's signatures count. */
function ReviewCommitment() {
  const [tenant, setTenant] = useState("");
  const [request, setRequest] = useState("");
  const ok = (v: string) => /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(v.trim());
  const review = useCellMutation((api, v: { t: string; r: string }) => api.finance.reviewCommitment(v.t, v.r), { success: "Review recorded. The customer's signatures now count.", onSuccess: () => setRequest("") });
  return (
    <Card title="Review non-standard commitment terms" description="Only a Controller or a CFO. Do this once the contract, legal and finance have read the terms the customer is signing.">
      <form
        className="flex flex-wrap items-end gap-3"
        onSubmit={(e: FormEvent) => {
          e.preventDefault();
          if (ok(tenant) && ok(request)) review.mutate({ t: tenant.trim().toLowerCase(), r: request.trim().toLowerCase() });
        }}
      >
        <TextField label="Organization tenant ID" value={tenant} onChange={setTenant} mono className="min-w-80" />
        <TextField label="Commitment request ID" value={request} onChange={setRequest} mono className="min-w-80" />
        <Button type="submit" isDisabled={!ok(tenant) || !ok(request) || review.isPending}>
          Mark reviewed
        </Button>
      </form>
      {review.isError ? (
        <div className="mt-3 space-y-3">
          <CapabilityHint error={review.error} capability={CAP} />
          <ApiErrorView error={review.error} title="The review was not recorded" onRetry={() => review.reset()} />
        </div>
      ) : null}
    </Card>
  );
}
