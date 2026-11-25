"use client";

import { useQueryClient } from "@tanstack/react-query";
import type { AgentCredentialItem, CredentialVerdict, IdpBinding, StopEvent, StopPlan, StopScope, ToolAction, ToolActionState } from "@zoiko/api-client";
import { Badge, Banner, Button, Card, DataTable, Dialog, EmptyState, Select, TextField, type Column } from "@zoiko/ui";
import { ClipboardCheck, KeyRound, OctagonX, ScrollText, ShieldAlert } from "lucide-react";
import { useState } from "react";

import { ApiErrorView, QueryView, useCellMutation, useCellQuery } from "@/lib/cell";

import {
  ACTION_STATE,
  BINDING_STATE,
  enforcementText,
  isMandateId,
  parsePublicKey,
  reasonText,
  RISK_TEXT,
  SCOPES,
  scopeInfo,
  signingText,
  STATUS_OPTIONS,
  stopProblem,
  unknownNote,
  VERDICT,
} from "./controls-logic";

const KEY = ["admin", "agents", "controls"];
const when = (iso: string | null): string => (iso ? new Date(iso).toLocaleString("en-GB") : "");

// ------------------------------------------------------------------------------- stops

/** Stop agents by scope. Every stop starts with a look at what it would reach; a stop denies NEW calls and cannot undo one that already ran; someone other than the person who stopped must close it. */
export function StopsSection() {
  const qc = useQueryClient();
  const stops = useCellQuery([...KEY, "stops"], (api) => api.agents.stops());
  const [scope, setScope] = useState<StopScope>("AGENT");
  const [subject, setSubject] = useState("");
  const [reason, setReason] = useState("");
  const [plan, setPlan] = useState<{ key: string; plan: StopPlan } | null>(null);
  const key = `${scope}|${subject.trim()}`;
  const info = scopeInfo(scope);
  const problem = stopProblem(scope, subject, reason);
  const look = useCellMutation<void, StopPlan>((api) => api.agents.stopPlan(scope, subject.trim() || undefined), { onSuccess: (p) => setPlan({ key, plan: p }) });
  const stop = useCellMutation<void, StopEvent>((api) => api.agents.stop(scope, subject.trim() || undefined, reason.trim()), {
    success: "Stopped. New calls are refused.",
    onSuccess: () => {
      setPlan(null);
      setReason("");
      void qc.invalidateQueries({ queryKey: KEY });
    },
  });
  const seen = plan?.key === key ? plan.plan : null;
  return (
    <div className="space-y-4">
      <Card title="Stop agents" description="Denies new calls from the agents you choose. It cannot undo a call that already ran.">
        <div className="space-y-3">
          <Select label="What to stop" options={SCOPES.map((s) => ({ id: s.id, label: s.label }))} value={scope} onChange={(v) => (setScope(v as StopScope), setPlan(null))} />
          <p className="text-xs text-ink-muted">{info.help}</p>
          {info.subjectLabel ? <TextField label={info.subjectLabel} value={subject} onChange={(v) => (setSubject(v), setPlan(null))} /> : null}
          <TextField label="Why" value={reason} onChange={setReason} description="The person who closes this stop reads it." />
          <div className="flex flex-wrap gap-2">
            <Button size="sm" variant="secondary" pending={look.isPending} isDisabled={!!problem && problem.startsWith("Enter")} onPress={() => look.mutate(undefined)}>
              See what it would do
            </Button>
            <Button size="sm" pending={stop.isPending} isDisabled={!seen || !!problem || seen.would_stop.agent_count === 0} onPress={() => stop.mutate(undefined)}>
              Stop now
            </Button>
          </div>
          {!seen ? <p className="text-xs text-ink-muted">Look at what it would do first. Stopping is enabled once you have.</p> : problem ? <p className="text-xs text-ink-muted">{problem}</p> : null}
          {seen ? (
            <div className="space-y-2 rounded border border-border p-3 text-sm" aria-label="What this stop would do">
              <p>
                <strong>{seen.would_stop.agent_count}</strong> {seen.would_stop.agent_count === 1 ? "agent" : "agents"} would stop, <strong>{seen.would_stop.live_mandate_count}</strong> live{" "}
                {seen.would_stop.live_mandate_count === 1 ? "mandate" : "mandates"} would pause, and <strong>{seen.would_stop.grants_not_yet_spent}</strong> unspent {seen.would_stop.grants_not_yet_spent === 1 ? "grant" : "grants"} would be refused when presented.
              </p>
              <p className="text-xs text-ink-muted">
                Not changed: {seen.would_not_change.actions_already_running_or_unknown} action(s) already running or unknown. {seen.would_not_change.note}
              </p>
              <p className="text-xs">{enforcementText(seen.enforcement.enforced_everywhere, seen.enforcement.unsupported.length)}</p>
            </div>
          ) : null}
          {look.error || stop.error ? <ApiErrorView error={look.error ?? stop.error} title="That did not work" onRetry={() => (look.reset(), stop.reset())} /> : null}
        </div>
      </Card>
      <QueryView query={stops}>
        {(d) => (
          <Card title="Stops" description="Newest first. Agents a stop reached stay stopped until someone other than its maker closes it.">
            {d.items.length === 0 ? (
              <EmptyState title="No stops" icon={OctagonX} compact>
                Nothing has been stopped.
              </EmptyState>
            ) : (
              <ul className="space-y-3">
                {d.items.map((e) => (
                  <StopRow key={e.event_id} e={e} />
                ))}
              </ul>
            )}
          </Card>
        )}
      </QueryView>
    </div>
  );
}

function StopRow({ e }: { e: StopEvent }) {
  const qc = useQueryClient();
  const [note, setNote] = useState("");
  const close = useCellMutation<void, StopEvent>((api) => api.agents.closeStop(e.event_id, note.trim()), { success: "Closed", onSuccess: () => void qc.invalidateQueries({ queryKey: KEY }) });
  return (
    <li className="space-y-2 rounded border border-border p-3 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone={e.state === "OPEN" ? "warn" : "neutral"}>{e.state === "OPEN" ? "Open" : "Closed"}</Badge>
        <span className="font-medium">
          Stop {e.epoch}: {scopeInfo(e.scope).label}
        </span>
        <span className="text-xs text-ink-muted">{when(e.effective_at)}</span>
      </div>
      <p className="text-xs">
        {e.targets.agent_count} agent(s) stopped, {e.targets.mandates_paused} mandate(s) paused{e.targets.bindings_revoked ? `, ${e.targets.bindings_revoked} provider binding(s) withdrawn` : ""}. {enforcementText(e.enforcement.enforced_everywhere, e.enforcement.unsupported.length)}
      </p>
      <p className="text-xs text-ink-muted">Reason: {e.reason}</p>
      {e.state === "OPEN" ? (
        <div className="flex flex-wrap items-end gap-2">
          <TextField label="Close this stop (not the person who made it)" value={note} onChange={setNote} />
          <Button size="sm" variant="secondary" isDisabled={note.trim().length < 3} pending={close.isPending} onPress={() => close.mutate(undefined)}>
            Close
          </Button>
        </div>
      ) : (
        <p className="text-xs text-ink-muted">Closed {when(e.closed_at)}: {e.close_note}</p>
      )}
      {close.error ? <ApiErrorView error={close.error} title="It was not closed" onRetry={() => close.reset()} /> : null}
    </li>
  );
}

// ------------------------------------------------------------------------------- what became of authorized calls

export function ActionsSection() {
  const [state, setState] = useState("");
  const [open, setOpen] = useState<string | null>(null);
  const list = useCellQuery([...KEY, "actions", state], (api) => api.agents.toolActions((state || undefined) as ToolActionState | undefined));
  const columns: Column<ToolAction>[] = [
    { key: "t", header: "Tool", pinned: true, render: (a) => <span className="font-mono text-xs">{a.tool_id}</span>, sortValue: (a) => a.tool_id },
    { key: "r", header: "On", render: (a) => <span className="text-xs">{a.resource}</span> },
    { key: "s", header: "What became of it", render: (a) => <Badge tone={ACTION_STATE[a.state].tone}>{ACTION_STATE[a.state].label}</Badge> },
    { key: "u", header: "Last change", render: (a) => when(a.updated_at), sortValue: (a) => a.updated_at },
  ];
  return (
    <>
      <QueryView query={list}>
        {(d) => (
          <Card title="What became of authorized calls" description="A grant says a call was allowed. The tool's own server then reports what happened. A receipt records both, and never reads as a success while the outcome is unknown." padded={false}>
            <div className="border-b border-border p-3">
              <Select label="State" options={STATUS_OPTIONS} value={state} onChange={setState} />
            </div>
            <DataTable<ToolAction>
              caption="Authorized tool calls"
              columns={columns}
              rows={d.items}
              rowKey={(a) => a.action_id}
              onRowAction={(a) => setOpen(a.action_id)}
              empty={
                <EmptyState title="Nothing here" icon={ScrollText} compact>
                  Calls appear when an agent is given a grant.
                </EmptyState>
              }
            />
          </Card>
        )}
      </QueryView>
      {open ? <ReceiptsDialog id={open} onClose={() => setOpen(null)} /> : null}
    </>
  );
}

function ReceiptsDialog({ id, onClose }: { id: string; onClose: () => void }) {
  const receipts = useCellQuery([...KEY, "receipts", id], (api) => api.agents.toolActionReceipts(id));
  return (
    <Dialog isOpen onOpenChange={(o) => !o && onClose()} title="Receipts" description="One for the authorization and one for each reported outcome. They cannot be edited." footer={<Button onPress={onClose}>Close</Button>}>
      <QueryView query={receipts}>
        {(d) => (
          <ul className="space-y-2 text-sm">
            {d.items.map((r) => (
              <li key={r.receipt_id} className="rounded border border-border p-2">
                <div className="flex flex-wrap items-center gap-2">
                  <Badge tone={ACTION_STATE[r.state].tone}>{ACTION_STATE[r.state].label}</Badge>
                  <span className="text-xs text-ink-muted">
                    #{r.revision} {when(r.issued_at)}
                  </span>
                  {r.signature_valid === false ? <Badge tone="danger">Does not verify</Badge> : <Badge tone="ok">Signature verifies</Badge>}
                </div>
                {r.state === "UNKNOWN" ? <p className="mt-1 text-xs">{unknownNote}</p> : null}
                <p className="mt-1 text-xs text-ink-muted">{r.payload.limits}</p>
              </li>
            ))}
          </ul>
        )}
      </QueryView>
    </Dialog>
  );
}

// ------------------------------------------------------------------------------- outside provider bindings

export function ProvidersSection() {
  const qc = useQueryClient();
  const list = useCellQuery([...KEY, "bindings"], (api) => api.agents.idpBindings());
  const [adding, setAdding] = useState(false);
  const [open, setOpen] = useState<IdpBinding | null>(null);
  const revoke = useCellMutation<string, IdpBinding>((api, id) => api.agents.revokeIdpBinding(id), {
    success: "Withdrawn",
    onSuccess: () => {
      setOpen(null);
      void qc.invalidateQueries({ queryKey: KEY });
    },
  });
  const columns: Column<IdpBinding>[] = [
    { key: "p", header: "Provider", pinned: true, render: (b) => b.profile_label },
    { key: "i", header: "Issuer", render: (b) => <span className="font-mono text-xs">{b.issuer}</span> },
    { key: "a", header: "Agent", render: (b) => <span className="font-mono text-xs">{b.agent_id}</span> },
    { key: "s", header: "State", render: (b) => <Badge tone={BINDING_STATE[b.state].tone}>{BINDING_STATE[b.state].label}</Badge> },
  ];
  return (
    <>
      <QueryView query={list}>
        {(d) => (
          <Card
            title="Outside identity providers"
            description="What a provider says about an agent is kept as evidence. It never activates an agent, asserts a corporate authority or grants anything."
            actions={
              <Button size="sm" icon={<KeyRound aria-hidden className="size-4" />} onPress={() => setAdding(true)}>
                Bind a provider
              </Button>
            }
            padded={false}
          >
            <DataTable<IdpBinding>
              caption="Provider bindings"
              columns={columns}
              rows={d.items}
              rowKey={(b) => b.binding_id}
              onRowAction={(b) => setOpen(b)}
              empty={
                <EmptyState title="No providers bound" icon={KeyRound} compact>
                  Bind an outside provider to an agent to keep what it says about that agent as evidence.
                </EmptyState>
              }
            />
            <p className="p-3 text-xs text-ink-muted">Okta and Entra are claim-name profiles tested against a local provider. They are not certified against a real Okta or Entra tenant.</p>
          </Card>
        )}
      </QueryView>
      {adding ? <BindDialog onClose={() => setAdding(false)} /> : null}
      {open ? (
        <Dialog
          isOpen
          onOpenChange={(o) => !o && !revoke.isPending && setOpen(null)}
          title={open.profile_label}
          description={BINDING_STATE[open.state].label}
          footer={
            <>
              {open.state !== "REVOKED" ? (
                <Button variant="secondary" pending={revoke.isPending} onPress={() => revoke.mutate(open.binding_id)}>
                  Withdraw
                </Button>
              ) : null}
              <Button onPress={() => setOpen(null)}>Close</Button>
            </>
          }
        >
          <div className="space-y-2 text-sm">
            <p className="font-mono text-xs">{open.issuer}</p>
            <p className="text-xs">Keys: {open.key_ids.join(", ")}. Last valid token: {when(open.last_ok_at) || "never"}.</p>
            {open.last_failure ? <Banner tone="warn" title="The last token failed a check">{open.last_failure}</Banner> : null}
            {Object.keys(open.external_claims).length ? <p className="text-xs">What the provider says: {Object.entries(open.external_claims).map(([k, v]) => `${k} ${v}`).join(", ")}. This is evidence only.</p> : null}
            {revoke.error ? <ApiErrorView error={revoke.error} title="It was not withdrawn" onRetry={() => revoke.reset()} /> : null}
          </div>
        </Dialog>
      ) : null}
    </>
  );
}

function BindDialog({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient();
  const [agent, setAgent] = useState("");
  const [profile, setProfile] = useState("generic_oidc");
  const [issuer, setIssuer] = useState("");
  const [subject, setSubject] = useState("");
  const [audience, setAudience] = useState("");
  const [keyText, setKeyText] = useState("");
  const parsed = keyText.trim() ? parsePublicKey(keyText) : null;
  const ready = agent.trim() && issuer.trim().startsWith("https://") && subject.trim() && audience.trim().length >= 3 && parsed?.key;
  const save = useCellMutation<void, IdpBinding>((api) => api.agents.createIdpBinding(agent.trim(), { profile, issuer: issuer.trim(), subject: subject.trim(), audience: audience.trim(), keys: [parsed!.key!] }), {
    success: "Bound as evidence",
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: KEY });
      onClose();
    },
  });
  return (
    <Dialog
      isOpen
      onOpenChange={(o) => !o && !save.isPending && onClose()}
      title="Bind a provider"
      description="Pin the provider's public key. A token from it can later be checked against that key and nothing else."
      footer={
        <>
          <Button variant="secondary" isDisabled={save.isPending} onPress={onClose}>
            Cancel
          </Button>
          <Button pending={save.isPending} isDisabled={!ready} onPress={() => save.mutate(undefined)}>
            Bind
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <TextField label="Agent ID" value={agent} onChange={setAgent} />
        <Select label="Provider" options={[{ id: "generic_oidc", label: "Generic OpenID Connect" }, { id: "okta", label: "Okta (not certified)" }, { id: "entra", label: "Microsoft Entra (not certified)" }]} value={profile} onChange={setProfile} />
        <TextField label="Issuer (https address)" value={issuer} onChange={setIssuer} />
        <TextField label="The provider's identifier for the agent" value={subject} onChange={setSubject} />
        <TextField label="Audience the provider uses" value={audience} onChange={setAudience} />
        <TextField label="The provider's public key (JSON)" value={keyText} onChange={setKeyText} multiline rows={4} description={parsed?.problem ?? "A key with a key id. Never a private key."} />
        {save.error ? <ApiErrorView error={save.error} title="It was not bound" onRetry={() => save.reset()} /> : null}
      </div>
    </Dialog>
  );
}

// ------------------------------------------------------------------------------- agent credentials and the verifier

export function CredentialsSection() {
  const qc = useQueryClient();
  const list = useCellQuery([...KEY, "credentials"], (api) => api.agents.agentCredentials());
  const [issuing, setIssuing] = useState(false);
  const [shown, setShown] = useState<AgentCredentialItem | null>(null);
  const revoke = useCellMutation<string, AgentCredentialItem>((api, id) => api.agents.revokeAgentCredential(id), { success: "Withdrawn", onSuccess: () => void qc.invalidateQueries({ queryKey: KEY }) });
  return (
    <div className="space-y-4">
      <QueryView query={list}>
        {(d) => (
          <Card
            title="Agent credentials"
            description="A short signed credential bound to the agent's own key. It carries no permission: it says who the agent is, not what it may do."
            actions={
              <Button size="sm" onPress={() => setIssuing(true)}>
                Issue a credential
              </Button>
            }
          >
            {d.items.length === 0 ? (
              <EmptyState title="No credentials" icon={KeyRound} compact>
                Issue one for an active agent, giving its public key.
              </EmptyState>
            ) : (
              <ul className="space-y-2 text-sm">
                {d.items.map((c) => (
                  <li key={c.credential_id} className="flex flex-wrap items-center gap-2 rounded border border-border p-2">
                    <Badge tone={c.status === "ACTIVE" ? "ok" : "danger"}>{c.status === "ACTIVE" ? "Good" : "Withdrawn"}</Badge>
                    <span className="font-mono text-xs">{c.agent_id}</span>
                    <span className="text-xs text-ink-muted">until {when(c.expires_at)}</span>
                    {c.status === "ACTIVE" ? (
                      <Button size="sm" variant="secondary" pending={revoke.isPending} onPress={() => revoke.mutate(c.credential_id)}>
                        Withdraw
                      </Button>
                    ) : null}
                  </li>
                ))}
              </ul>
            )}
            {revoke.error ? <ApiErrorView error={revoke.error} title="It was not withdrawn" onRetry={() => revoke.reset()} /> : null}
          </Card>
        )}
      </QueryView>
      <VerifyCard />
      {issuing ? <IssueDialog onClose={() => setIssuing(false)} onIssued={(c) => (setIssuing(false), setShown(c))} /> : null}
      {shown ? (
        <Dialog isOpen onOpenChange={(o) => !o && setShown(null)} title="Credential issued" description="This is shown once. Give it to the agent." footer={<Button onPress={() => setShown(null)}>Done</Button>}>
          <pre className="max-h-48 overflow-auto break-all whitespace-pre-wrap rounded bg-sunken p-2 text-xs">{shown.credential}</pre>
        </Dialog>
      ) : null}
    </div>
  );
}

function IssueDialog({ onClose, onIssued }: { onClose: () => void; onIssued: (c: AgentCredentialItem) => void }) {
  const qc = useQueryClient();
  const [agent, setAgent] = useState("");
  const [keyText, setKeyText] = useState("");
  const [audience, setAudience] = useState("");
  const parsed = keyText.trim() ? parsePublicKey(keyText) : null;
  const save = useCellMutation<void, AgentCredentialItem>((api) => api.agents.issueAgentCredential(agent.trim(), { public_key: parsed!.key!, audience: audience.trim() || undefined }), {
    onSuccess: (c) => {
      void qc.invalidateQueries({ queryKey: KEY });
      onIssued(c);
    },
  });
  return (
    <Dialog
      isOpen
      onOpenChange={(o) => !o && !save.isPending && onClose()}
      title="Issue a credential"
      footer={
        <>
          <Button variant="secondary" isDisabled={save.isPending} onPress={onClose}>
            Cancel
          </Button>
          <Button pending={save.isPending} isDisabled={!agent.trim() || !parsed?.key} onPress={() => save.mutate(undefined)}>
            Issue
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <TextField label="Agent ID" value={agent} onChange={setAgent} />
        <TextField label="The agent's public key (JSON)" value={keyText} onChange={setKeyText} multiline rows={4} description={parsed?.problem ?? "With a key id. Never a private key."} />
        <TextField label="Relying party it is for (optional)" value={audience} onChange={setAudience} />
        {save.error ? <ApiErrorView error={save.error} title="It was not issued" onRetry={() => save.reset()} /> : null}
      </div>
    </Dialog>
  );
}

function VerifyCard() {
  const [credential, setCredential] = useState("");
  const [presentation, setPresentation] = useState("");
  const [party, setParty] = useState("");
  const [nonce, setNonce] = useState("");
  const [out, setOut] = useState<CredentialVerdict | null>(null);
  const check = useCellMutation<void, CredentialVerdict>(
    (api) => api.agents.verifyAgentCredential({ credential: credential.trim(), relying_party: party.trim(), presentation: presentation.trim() || undefined, nonce: nonce.trim() || undefined }),
    { onSuccess: setOut },
  );
  const v = out ? VERDICT[out.verdict] : null;
  return (
    <Card title="Check a credential" description="Checks the signature and the live standing of the credential and the agent. Add a presentation to also check the presenter holds the agent's key.">
      <div className="space-y-3">
        <TextField label="Credential" value={credential} onChange={setCredential} multiline rows={3} />
        <TextField label="Who is asking (relying party)" value={party} onChange={setParty} />
        <TextField label="Presentation (optional)" value={presentation} onChange={setPresentation} multiline rows={3} />
        <TextField label="Nonce you gave the presenter (with a presentation)" value={nonce} onChange={setNonce} />
        <Button size="sm" pending={check.isPending} isDisabled={credential.trim().length < 20 || party.trim().length < 3} onPress={() => check.mutate(undefined)}>
          Check
        </Button>
        {v && out ? (
          <Banner tone={v.tone === "ok" ? "ok" : v.tone === "warn" ? "warn" : "danger"} title={v.label} live="polite">
            {v.text}
            {out.reason_codes.length ? <ul className="mt-1 list-disc pl-4 text-xs">{out.reason_codes.map((c) => <li key={c}>{reasonText(c)}</li>)}</ul> : null}
          </Banner>
        ) : null}
        {check.error ? <ApiErrorView error={check.error} title="It could not be checked" onRetry={() => check.reset()} /> : null}
      </div>
    </Card>
  );
}

// ------------------------------------------------------------------------------- risk signals

export function RiskSection() {
  const report = useCellQuery([...KEY, "risk"], (api) => api.agents.riskSignals());
  return (
    <QueryView query={report}>
      {(d) => (
        <Card title="Places worth a look" description={RISK_TEXT}>
          {d.signals.length === 0 ? (
            <EmptyState title="No signals" icon={ShieldAlert} compact>
              Nothing stands out. That is not proof that nothing is wrong.
            </EmptyState>
          ) : (
            <ul className="space-y-2 text-sm">
              {d.signals.map((s) => (
                <li key={`${s.kind}${s.subject}`} className="rounded border border-border p-2">
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge tone={s.severity === "attention" ? "warn" : "info"}>{s.severity === "attention" ? "Look at this" : "For information"}</Badge>
                    <span className="font-mono text-xs">{s.subject}</span>
                    {s.since ? <span className="text-xs text-ink-muted">since {when(s.since)}</span> : null}
                  </div>
                  <p className="mt-1 text-xs">{s.text}</p>
                </li>
              ))}
            </ul>
          )}
          <div className="mt-3 text-xs text-ink-muted">
            <p className="font-medium">What this cannot see</p>
            <ul className="list-disc pl-4">{d.cannot_see.map((c) => <li key={c}>{c}</li>)}</ul>
          </div>
        </Card>
      )}
    </QueryView>
  );
}

// ------------------------------------------------------------------------------- mandate signing

export function MandateSigningSection() {
  const qc = useQueryClient();
  const [id, setId] = useState("");
  const [loaded, setLoaded] = useState<string | null>(null);
  const valid = isMandateId(id);
  const view = useCellQuery([...KEY, "mandate", loaded ?? ""], (api) => api.agents.mandateSigning(loaded!), { enabled: !!loaded });
  const sign = useCellMutation<"sign" | "countersign", unknown>((api, role) => api.agents.signMandate(loaded!, view.data!.canonical_hash, role), {
    success: "Confirmed",
    onSuccess: () => void qc.invalidateQueries({ queryKey: [...KEY, "mandate", loaded ?? ""] }),
  });
  return (
    <Card title="Confirm a mandate's exact terms" description="You confirm a fingerprint of the terms you read. If a term changes, it is a different mandate and needs its own confirmations. A mandate that can spend needs a second, independent person.">
      <div className="space-y-3">
        <div className="flex flex-wrap items-end gap-2">
          <TextField label="Mandate ID" value={id} onChange={setId} />
          <Button size="sm" variant="secondary" isDisabled={!valid} onPress={() => setLoaded(id.trim())}>
            Read it
          </Button>
        </div>
        {loaded ? (
          <QueryView query={view}>
            {(d) => (
              <div className="space-y-3 text-sm">
                <Banner tone={d.signing.complete ? "ok" : "info"} title={d.signing.complete ? "Fully confirmed" : "Not yet fully confirmed"}>
                  {signingText(d.signing)}
                </Banner>
                <pre className="max-h-56 overflow-auto rounded bg-sunken p-2 text-xs">{JSON.stringify(d.canonical_terms, null, 2)}</pre>
                <p className="break-all font-mono text-xs">{d.canonical_hash}</p>
                <ul className="space-y-1 text-xs">
                  {d.signatures.map((s) => (
                    <li key={`${s.role}${s.signed_at}`}>
                      {s.role === "GRANTOR" ? "Grantor" : "Second person"} confirmed {when(s.signed_at)}
                      {s.current ? "" : " (an earlier version of the terms)"}
                      {s.signature_valid === false ? " (does not verify)" : ""}
                    </li>
                  ))}
                </ul>
                <div className="flex flex-wrap gap-2">
                  <Button size="sm" pending={sign.isPending} isDisabled={!d.signing.missing.includes("GRANTOR")} onPress={() => sign.mutate("sign")}>
                    Confirm as the person who handed on their authority
                  </Button>
                  <Button size="sm" variant="secondary" pending={sign.isPending} isDisabled={!d.signing.missing.includes("APPROVER")} onPress={() => sign.mutate("countersign")}>
                    Give the second confirmation
                  </Button>
                </div>
                <p className="text-xs text-ink-muted">{d.note}</p>
                {sign.error ? <ApiErrorView error={sign.error} title="That was not recorded" onRetry={() => sign.reset()} /> : null}
              </div>
            )}
          </QueryView>
        ) : (
          <EmptyState title="Enter a mandate" icon={ClipboardCheck} compact>
            Paste a mandate ID to read its exact terms.
          </EmptyState>
        )}
      </div>
    </Card>
  );
}
