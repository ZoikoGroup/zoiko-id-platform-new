"use client";

import { useQueryClient } from "@tanstack/react-query";
import type { VerifyApiGoLive } from "@zoiko/api-client";
import { Badge, Banner, Button, Card, Checkbox, Dialog, Select, TextField } from "@zoiko/ui";
import { CheckCircle2, Circle, Rocket } from "lucide-react";
import { useState } from "react";

import { ApiErrorView, QueryView, useCellMutation, useCellQuery } from "@/lib/cell";

import { draftProblem, EMPTY_DRAFT, methodLabel, purposeLabel, REQUEST_STATE, toRequest, type GoLiveDraft } from "./go-live-logic";

const KEY = ["verify-api", "go-live"];
const when = (iso: string | null | undefined): string => (iso ? new Date(iso).toLocaleString("en-GB") : "-");

/** Going live (CR-153): what you still need to test, the request, and what was approved. Approval is made by our team after your sandbox checks pass and your company, terms and markets are confirmed.
 * Approval lets you create production applications; the digest shown is the fingerprint of exactly what was approved. */
export function GoLive({ onCreateProduction }: { onCreateProduction: () => void }) {
  const q = useCellQuery(KEY, (api) => api.verifyApi.goLive());
  const [asking, setAsking] = useState(false);
  return (
    <QueryView query={q}>
      {(d) => (
        <Card
          title="Go live"
          description={
            d.self_serve?.enabled
              ? "Production opens by itself once your own identity check and your company check are complete and your plan is paid. The sandbox is free and optional: use it to practise, or go straight to production."
              : "Production opens after you have tested in the sandbox and our team has confirmed your company, your terms and the countries you will serve."
          }
          actions={d.grant ? <Badge tone="ok">Production open</Badge> : <Badge tone="info">Sandbox only</Badge>}
        >
          <div className="space-y-4">
            {d.grant ? <Granted d={d} onCreateProduction={onCreateProduction} /> : <Asking d={d} onAsk={() => setAsking(true)} />}
          </div>
          {asking ? <AskDialog d={d} onClose={() => setAsking(false)} selfServe={Boolean(d.self_serve?.enabled)} /> : null}
        </Card>
      )}
    </QueryView>
  );
}

function Granted({ d, onCreateProduction }: { d: VerifyApiGoLive; onCreateProduction: () => void }) {
  const g = d.grant!;
  return (
    <>
      <Banner tone="ok" title="Production is open for your company">
        Approved on {when(g.approved_at)} for {g.markets.join(", ")}: {g.methods.map(methodLabel).join(", ").toLowerCase()}. You can now create production applications. Live verifications are switched on
        separately when the live service is connected.
      </Banner>
      <p className="text-xs text-ink-muted">
        Fingerprint of what was approved: <span className="font-mono break-all">{g.manifest_digest}</span>
      </p>
      <p className="text-xs text-ink-muted">{g.signed ? "This approval is signed by Zoiko iD." : "This approval was made before signing existed and carries no signature."}</p>
      <Button size="sm" icon={<Rocket aria-hidden className="size-4" />} onPress={onCreateProduction}>
        Create a production application
      </Button>
    </>
  );
}

/** The three things that open production by themselves, each with where to finish it. The button opens only when all three are done; there is no sandbox test and no waiting for a person. */
function SelfServe({ d, onAsk }: { d: VerifyApiGoLive; onAsk: () => void }) {
  const s = d.self_serve!;
  const rows: { done: boolean; label: string; help: string; href: string; link: string }[] = [
    { done: s.identity, label: "Your own identity is verified", help: "Photograph your document and yourself once.", href: "/account/identity-proofing", link: "Verify my identity" },
    { done: s.company, label: "Your company is verified", help: "Claim your company and finish its check.", href: "/admin/organization", link: "Verify my company" },
    { done: s.paid_plan, label: "Your plan is paid", help: "Production is part of a paid plan. The free plan has the sandbox only.", href: "/admin/billing", link: "Choose a plan" },
  ];
  return (
    <>
      <section aria-label="What opens production" className="space-y-2">
        <h3 className="text-sm font-semibold text-ink">Three things open production</h3>
        <ul className="space-y-2">
          {rows.map((r) => (
            <li key={r.label} className="flex items-start gap-2 text-sm">
              {r.done ? <CheckCircle2 aria-hidden className="mt-0.5 size-4 text-ok-fg" /> : <Circle aria-hidden className="mt-0.5 size-4 text-ink-muted" />}
              <span>
                <span className={r.done ? "text-ink" : "font-medium text-ink"}>{r.label}</span>
                {r.done ? (
                  <span className="sr-only"> (done)</span>
                ) : (
                  <span className="block text-xs text-ink-muted">
                    {r.help}{" "}
                    <a className="font-medium text-brand-700 underline" href={r.href}>
                      {r.link}
                    </a>
                  </span>
                )}
              </span>
            </li>
          ))}
        </ul>
      </section>
      <Button size="sm" icon={<Rocket aria-hidden className="size-4" />} isDisabled={!s.ready} onPress={onAsk}>
        Open production
      </Button>
      {!s.ready ? <p className="text-xs text-ink-muted">The button opens when all three are done. You can still use the sandbox meanwhile.</p> : null}
    </>
  );
}

function Asking({ d, onAsk }: { d: VerifyApiGoLive; onAsk: () => void }) {
  const qc = useQueryClient();
  const withdraw = useCellMutation((api, id: string) => api.verifyApi.withdrawGoLive(id), {
    success: "Request withdrawn",
    onSuccess: () => void qc.invalidateQueries({ queryKey: KEY }),
  });
  const r = d.request;
  const open = r && (r.status === "SUBMITTED" || r.status === "IN_REVIEW");
  const ready = d.certification.every((c) => c.met);
  if (d.self_serve?.enabled && !open) return <SelfServe d={d} onAsk={onAsk} />;
  if (open && r) {
    const s = REQUEST_STATE[r.status]!;
    return (
      <>
        <div className="flex flex-wrap items-center gap-2">
          <Badge tone={s.tone}>{s.label}</Badge>
          <span className="text-sm text-ink-muted">Sent {when(r.created_at)}</span>
        </div>
        <p className="text-sm text-ink-muted">{s.text}</p>
        <p className="text-xs text-ink-muted">
          You asked for {r.markets.join(", ")}: {r.methods.map(methodLabel).join(", ").toLowerCase()}. {purposeLabel(r.purpose_code)}.
        </p>
        <Button size="sm" variant="secondary" isDisabled={withdraw.isPending} onPress={() => withdraw.mutate(r.request_id)}>
          Withdraw request
        </Button>
        {withdraw.error ? <ApiErrorView error={withdraw.error} title="The request was not withdrawn" onRetry={() => withdraw.reset()} /> : null}
      </>
    );
  }
  return (
    <>
      {r && r.status === "DECLINED" ? (
        <Banner tone="danger" title="Your last request was not approved">
          {r.note ?? "No reason was given."}
        </Banner>
      ) : null}
      <section aria-label="Sandbox checks" className="space-y-2">
        <h3 className="text-sm font-semibold text-ink">Before you ask</h3>
        <ul className="space-y-1.5">
          {d.certification.map((c) => (
            <li key={c.id} className="flex items-start gap-2 text-sm">
              {c.met ? <CheckCircle2 aria-hidden className="mt-0.5 size-4 text-ok-fg" /> : <Circle aria-hidden className="mt-0.5 size-4 text-ink-muted" />}
              <span>
                <span className={c.met ? "text-ink" : "font-medium text-ink"}>{c.label}</span>
                {c.met ? <span className="sr-only"> (done)</span> : <span className="block text-xs text-ink-muted">{c.help}</span>}
              </span>
            </li>
          ))}
          <li className="flex items-start gap-2 text-sm">
            {d.company.verified ? <CheckCircle2 aria-hidden className="mt-0.5 size-4 text-ok-fg" /> : <Circle aria-hidden className="mt-0.5 size-4 text-ink-muted" />}
            <span>
              <span className={d.company.verified ? "text-ink" : "font-medium text-ink"}>Your company is verified against its registry</span>
              {d.company.verified ? (
                <span className="sr-only"> (done)</span>
              ) : (
                <span className="block text-xs text-ink-muted">
                  You can still ask, but we cannot approve production until your company verification is complete. Finish it under Organization.
                </span>
              )}
            </span>
          </li>
        </ul>
      </section>
      <Button size="sm" icon={<Rocket aria-hidden className="size-4" />} isDisabled={!ready} onPress={onAsk}>
        Ask to go live
      </Button>
      {!ready ? <p className="text-xs text-ink-muted">The button opens when all three are done. Use the Test Lab inside an application.</p> : null}
    </>
  );
}

function AskDialog({ d, onClose, selfServe = false }: { d: VerifyApiGoLive; onClose: () => void; selfServe?: boolean }) {
  const qc = useQueryClient();
  const [draft, setDraft] = useState<GoLiveDraft>(EMPTY_DRAFT);
  const set = (p: Partial<GoLiveDraft>) => setDraft((x) => ({ ...x, ...p }));
  const problem = draftProblem(draft);
  const send = useCellMutation((api) => api.verifyApi.askToGoLive(toRequest(draft, { terms: d.terms_version, dpa: d.dpa_version })), {
    success: selfServe ? "Production is open" : "Request sent",
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: KEY });
      onClose();
    },
  });
  return (
    <Dialog
      isOpen
      onOpenChange={(o) => !o && onClose()}
      title={selfServe ? "Open production" : "Ask to go live"}
      description={selfServe ? "Tell us where and how you will use verification. Production opens as soon as you send this." : "Tell us where and how you will use verification. We review it before production opens."}
      size="lg"
      footer={
        <>
          <Button variant="secondary" onPress={onClose}>
            Cancel
          </Button>
          <Button isDisabled={problem !== null || send.isPending} pending={send.isPending} onPress={() => send.mutate(undefined)}>
            {selfServe ? "Open production" : "Send request"}
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <TextField
          label="Countries you will serve"
          description="Two-letter codes, for example GB, US, FR. Up to 20."
          value={draft.markets}
          onChange={(v) => set({ markets: v })}
        />
        <fieldset className="space-y-1.5">
          <legend className="text-sm font-medium text-ink">Checks you will use</legend>
          {d.options.methods.map((m) => (
            <Checkbox key={m} isSelected={draft.methods.includes(m)} onChange={(on) => set({ methods: on ? [...draft.methods, m] : draft.methods.filter((x) => x !== m) })}>
              {methodLabel(m)}
            </Checkbox>
          ))}
        </fieldset>
        <Select label="What you will use it for" options={d.options.purposes.map((p) => ({ id: p, label: purposeLabel(p) }))} value={draft.purpose || null} onChange={(v) => set({ purpose: v })} />
        <Select label="How many verifications you expect" options={d.options.volume_bands} value={draft.band || null} onChange={(v) => set({ band: v })} />
        <TextField
          label="In your own words"
          description="What your product does and who you verify. 10 to 500 characters."
          multiline
          rows={3}
          value={draft.useCase}
          onChange={(v) => set({ useCase: v })}
        />
        <div className="space-y-1.5">
          <Checkbox isSelected={draft.terms} onChange={(v) => set({ terms: v })}>
            I accept the Verify API terms (version {d.terms_version}) for my company.
          </Checkbox>
          <Checkbox isSelected={draft.dpa} onChange={(v) => set({ dpa: v })}>
            I accept the data-processing terms (version {d.dpa_version}).
          </Checkbox>
        </div>
        {problem ? <p className="text-xs text-ink-muted">{problem}</p> : null}
        {send.error ? <ApiErrorView error={send.error} title="The request was not sent" onRetry={() => send.reset()} /> : null}
      </div>
    </Dialog>
  );
}
