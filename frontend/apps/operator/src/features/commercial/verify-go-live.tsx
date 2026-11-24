"use client";

import type { VerifyApiAttestations, VerifyApiGoLiveOperatorRequest } from "@zoiko/api-client";
import { Badge, Banner, Button, Card, Checkbox, EmptyState, TextField } from "@zoiko/ui";
import { Rocket } from "lucide-react";
import { useState } from "react";

import { ApiErrorView, useCellMutation, useCellQuery } from "@/lib/op";

import { COMMERCIAL_KEY } from "./keys";

const KEY = [...COMMERCIAL_KEY, "verify-go-live"];
const when = (iso: string | null): string => (iso ? new Date(iso).toLocaleString("en-GB") : "-");

/** What a person at Zoiko confirms before production opens for a partner. Every item must be ticked; none is assumed. The words are what the approval record keeps. */
export const CONFIRMATIONS: { id: keyof VerifyApiAttestations; label: string }[] = [
  { id: "company_verified", label: "The company's identity and authority have been verified" },
  { id: "terms_signed", label: "The commercial terms and data-processing terms are signed" },
  { id: "privacy_reviewed", label: "Privacy has reviewed the markets, purpose and retention" },
  { id: "billing_ready", label: "Billing is set up for the expected volume" },
  { id: "provider_approved", label: "The check provider is approved for each requested market and method" },
];

const NONE: VerifyApiAttestations = { company_verified: false, terms_signed: false, privacy_reviewed: false, billing_ready: false, provider_approved: false };
export const allConfirmed = (c: VerifyApiAttestations): boolean => CONFIRMATIONS.every((x) => c[x.id]);

/** Partners asking to open production (CR-153). One person takes a request into review; a DIFFERENT person confirms every item and approves. Approval records exactly what was allowed. */
export function VerifyGoLiveView() {
  const list = useCellQuery(KEY, (api) => api.commercial.goLiveRequests());
  return (
    <div className="space-y-4">
      <Card title="Verify API go-live requests" description="Companies asking to open production. Oldest first. The person who takes a request into review cannot approve it.">
        {list.isPending ? (
          <p className="text-sm text-ink-muted">Loading…</p>
        ) : list.error ? (
          <ApiErrorView error={list.error} title="The requests could not be loaded" onRetry={() => void list.refetch()} />
        ) : list.data.items.length === 0 ? (
          <EmptyState title="Nothing waiting" icon={Rocket} compact>
            No company is waiting to go live.
          </EmptyState>
        ) : (
          <div className="space-y-4">
            {list.data.items.map((r) => (
              <RequestPanel key={r.request_id} r={r} />
            ))}
          </div>
        )}
      </Card>
      <StopProduction />
    </div>
  );
}

function RequestPanel({ r }: { r: VerifyApiGoLiveOperatorRequest }) {
  const [confirmed, setConfirmed] = useState<VerifyApiAttestations>(NONE);
  const [note, setNote] = useState("");
  const opts = { invalidate: [KEY] };
  const review = useCellMutation<void, unknown>((api) => api.commercial.goLiveReview(r.request_id), { ...opts, success: "Taken into review" });
  const approve = useCellMutation<void, unknown>((api) => api.commercial.goLiveApprove(r.request_id, confirmed, note), { ...opts, success: "Approved. Production is open for the company." });
  const decline = useCellMutation<void, unknown>((api) => api.commercial.goLiveDecline(r.request_id, note), { ...opts, success: "Declined. The company reads the reason." });
  const busy = review.isPending || approve.isPending || decline.isPending;
  const error = review.error ?? approve.error ?? decline.error;
  return (
    <section aria-label={`Request from ${r.tenant_id}`} className="space-y-3 rounded-lg border border-border p-4">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-mono text-xs">{r.tenant_id}</span>
        <Badge tone={r.status === "IN_REVIEW" ? "warn" : "info"}>{r.status === "IN_REVIEW" ? "In review" : "Waiting"}</Badge>
        <span className="text-xs text-ink-muted">Asked {when(r.created_at)}</span>
      </div>
      <dl className="grid gap-x-6 gap-y-1 text-sm sm:grid-cols-2">
        <div><dt className="text-xs text-ink-muted">Countries</dt><dd>{r.markets.join(", ")}</dd></div>
        <div><dt className="text-xs text-ink-muted">Checks</dt><dd>{r.methods.join(", ").replace(/_/g, " ")}</dd></div>
        <div><dt className="text-xs text-ink-muted">Purpose</dt><dd>{r.purpose_code.replace(/_/g, " ")}</dd></div>
        <div><dt className="text-xs text-ink-muted">Expected volume</dt><dd>{r.volume_band.replace(/_/g, " ")}</dd></div>
        <div className="sm:col-span-2"><dt className="text-xs text-ink-muted">In their words</dt><dd className="whitespace-pre-wrap">{r.use_case}</dd></div>
        <div className="sm:col-span-2">
          <dt className="text-xs text-ink-muted">Sandbox checks when they asked</dt>
          <dd>{r.certification.map((c) => `${c.met ? "✓" : "✗"} ${c.label}`).join("  ·  ")}</dd>
        </div>
      </dl>
      {r.status === "SUBMITTED" ? (
        <Button size="sm" isDisabled={busy} onPress={() => review.mutate(undefined)}>
          Take into review
        </Button>
      ) : (
        <div className="space-y-3">
          <fieldset className="space-y-1.5">
            <legend className="text-sm font-medium text-ink">Confirm each item yourself</legend>
            {CONFIRMATIONS.map((c) => (
              <Checkbox key={c.id} isSelected={confirmed[c.id]} onChange={(on) => setConfirmed({ ...confirmed, [c.id]: on })}>
                {c.label}
              </Checkbox>
            ))}
          </fieldset>
          <TextField label="Note (the company reads it if you decline)" value={note} onChange={setNote} />
          <div className="flex flex-wrap gap-2">
            <Button size="sm" isDisabled={busy || !allConfirmed(confirmed)} pending={approve.isPending} onPress={() => approve.mutate(undefined)}>
              Approve and open production
            </Button>
            <Button size="sm" variant="secondary" isDisabled={busy || note.trim().length < 3} onPress={() => decline.mutate(undefined)}>
              Decline
            </Button>
          </div>
        </div>
      )}
      {error ? <ApiErrorView error={error} title="That was not recorded" onRetry={() => (review.reset(), approve.reset(), decline.reset())} /> : null}
    </section>
  );
}

/** The emergency stop: ends a company's production access and suspends every production application and its tokens at once. */
function StopProduction() {
  const [tenant, setTenant] = useState("");
  const [reason, setReason] = useState("");
  const [confirm, setConfirm] = useState(false);
  const stop = useCellMutation<void, { applications_stopped: number }>((api) => api.commercial.revokeProductionGrant(tenant, reason), {
    invalidate: [KEY],
    success: "Production stopped for the company",
  });
  const ok = /^[0-9a-f-]{36}$/i.test(tenant.trim()) && reason.trim().length >= 3 && confirm;
  return (
    <Card title="Stop production for a company" description="Ends the company's production access and suspends all its production applications at once. Use it for misuse or a security concern.">
      <div className="space-y-3">
        <Banner tone="warn" title="This takes effect immediately">
          Every production token the company holds stops working. The company can ask to go live again afterwards.
        </Banner>
        <TextField label="Company ID" value={tenant} onChange={setTenant} />
        <TextField label="Reason (recorded)" value={reason} onChange={setReason} />
        <Checkbox isSelected={confirm} onChange={setConfirm}>
          I understand this stops their production verifications now.
        </Checkbox>
        <Button size="sm" variant="danger" isDisabled={!ok || stop.isPending} pending={stop.isPending} onPress={() => stop.mutate(undefined)}>
          Stop production
        </Button>
        {stop.error ? <ApiErrorView error={stop.error} title="Production was not stopped" onRetry={() => stop.reset()} /> : null}
      </div>
    </Card>
  );
}
