"use client";

import { Banner, Button, Card, Select, TextField } from "@zoiko/ui";
import { useState } from "react";

import { useCellMutation } from "@/lib/op";

import { RegError } from "./common";
import {
  AGE_OPTIONS,
  buildPack,
  defaultForm,
  DOCUMENTS,
  gaps,
  LEGAL_FORMS,
  MARKETS,
  RECHECK_K1,
  RECHECK_K2,
  RECORD_CLASSES,
  RETENTION_OPTIONS,
  RISK_TIERS,
  SOURCES,
  UBO_THRESHOLDS,
  type PackForm,
} from "./kyb-pack-form";
import { problemText } from "./kyb-policy-logic";

const KEY = ["regulatory", "kyb-policies"] as const;

function Tick({ checked, onChange, children }: { checked: boolean; onChange: (v: boolean) => void; children: React.ReactNode }) {
  return (
    <label className="flex items-start gap-2">
      <input type="checkbox" className="mt-1" checked={checked} onChange={(e) => onChange(e.target.checked)} />
      <span>{children}</span>
    </label>
  );
}

/** The pack as choices: pick the market, tick the sources and documents, pick periods from lists. The platform still names anything incomplete once it is stored. */
export function PackBuilder({ onStored }: { onStored: (hash: string) => void }) {
  const [f, setF] = useState<PackForm>(defaultForm);
  const set = <K extends keyof PackForm>(k: K, v: PackForm[K]) => setF((cur) => ({ ...cur, [k]: v }));
  const propose = useCellMutation((api, pack: Record<string, unknown>) => api.regulatory.kybPolicyPropose(pack), {
    invalidate: [KEY as never],
    success: "Version stored",
    onSuccess: (r) => onStored(r.policy_hash),
  });
  const todo = gaps(f);

  return (
    <Card title="Propose a version" description="Choose from the lists. The platform names everything that is incomplete; nothing is in force until it is signed in four roles and published.">
      <div className="space-y-6 text-sm">
        <section className="space-y-3">
          <p className="font-medium">1. Which market</p>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <Select label="Country" value={f.jurisdiction} onChange={(v) => set("jurisdiction", v)} options={MARKETS} />
            <Select label="Legal form" value={f.legalForm} onChange={(v) => set("legalForm", v)} options={LEGAL_FORMS} />
            <Select label="Risk tier" value={f.riskTier} onChange={(v) => set("riskTier", v)} options={RISK_TIERS.map((r) => ({ id: r, label: r.charAt(0) + r.slice(1).toLowerCase() }))} />
            <TextField label="Version" value={f.version} onChange={(v) => set("version", v)} />
          </div>
        </section>

        <section className="space-y-3">
          <p className="font-medium">2. Sources it may use</p>
          {SOURCES.map((s) => {
            const v = f.sources[s.adapter]!;
            const upd = (patch: Partial<typeof v>) => set("sources", { ...f.sources, [s.adapter]: { ...v, ...patch } });
            return (
              <div key={s.adapter} className="rounded border p-3">
                <Tick checked={v.on} onChange={(on) => upd({ on })}>
                  {s.label} <span className="text-ink-muted">· {s.help}</span>
                </Tick>
                {v.on ? (
                  <div className="mt-3 grid gap-3 sm:grid-cols-3">
                    <TextField label="Lawful-use record" description="Reference" value={v.legalBasis} onChange={(x) => upd({ legalBasis: x })} />
                    {s.credentialRequired ? <TextField label="Key in the key store" description="Its name, never the secret" value={v.credentialRef} onChange={(x) => upd({ credentialRef: x })} /> : <div />}
                    <TextField label="Terms of use" description="Version" value={v.terms} onChange={(x) => upd({ terms: x })} />
                  </div>
                ) : null}
              </div>
            );
          })}
        </section>

        <section className="space-y-3">
          <p className="font-medium">3. Documents it accepts, and how old they may be</p>
          {DOCUMENTS.map((d) => {
            const v = f.documents[d.docClass]!;
            const upd = (patch: Partial<typeof v>) => set("documents", { ...f.documents, [d.docClass]: { ...v, ...patch } });
            return (
              <div key={d.docClass} className="flex flex-wrap items-end gap-3 rounded border p-3">
                <div className="min-w-0 flex-1">
                  <Tick checked={v.on} onChange={(on) => upd({ on })}>
                    {d.label} <span className="text-ink-muted">· {d.help}</span>
                  </Tick>
                </div>
                {v.on ? <Select label="May be at most" value={v.ageDays} onChange={(x) => upd({ ageDays: x })} options={AGE_OPTIONS} /> : null}
              </div>
            );
          })}
          <Select className="max-w-xs" label="Reviewers when a document decides" value={f.reviewers} onChange={(v) => set("reviewers", v)} options={[{ id: "2", label: "2 independent reviewers" }, { id: "3", label: "3 independent reviewers" }]} />
        </section>

        <section className="space-y-3">
          <p className="font-medium">4. Ownership and control</p>
          <div className="grid gap-3 sm:grid-cols-3">
            <Select label="Owners counted from" value={f.uboPercent} onChange={(v) => set("uboPercent", v)} options={UBO_THRESHOLDS} />
            <TextField label="Signed by (Compliance owner)" value={f.uboSignedBy} onChange={(v) => set("uboSignedBy", v)} />
          </div>
          <Tick checked={f.uboOtherControl} onChange={(v) => set("uboOtherControl", v)}>Also assess control by other means (votes, nominees, trusts)</Tick>
        </section>

        <section className="space-y-3">
          <p className="font-medium">5. How long records are kept</p>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {RECORD_CLASSES.map((r) => (
              <Select key={r.id} label={r.label} value={f.retentionDays[r.id] ?? "365"} onChange={(v) => set("retentionDays", { ...f.retentionDays, [r.id]: v })} options={RETENTION_OPTIONS} />
            ))}
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            <TextField label="Legal basis for the schedule" value={f.retentionBasis} onChange={(v) => set("retentionBasis", v)} />
            <TextField label="Deletion job" description="Its reference" value={f.deletionJob} onChange={(v) => set("deletionJob", v)} />
          </div>
          <Tick checked={f.legalHold} onChange={(v) => set("legalHold", v)}>Legal holds are supported</Tick>
        </section>

        <section className="space-y-3">
          <p className="font-medium">6. When a verified company is checked again</p>
          <div className="grid gap-3 sm:grid-cols-2">
            <Select label="Documents reviewed (K1)" value={f.recheckK1} onChange={(v) => set("recheckK1", v)} options={RECHECK_K1} />
            <Select label="Registry matched (K2)" value={f.recheckK2} onChange={(v) => set("recheckK2", v)} options={RECHECK_K2} />
          </div>
        </section>

        {todo.length ? (
          <Banner tone="info" title="Still to fill in before this pack is complete">
            <ul className="list-disc pl-5">{todo.map((t) => <li key={t}>{t}</li>)}</ul>
            <p className="mt-1">You can store it now and finish later. It stays off until it is complete, signed and published.</p>
          </Banner>
        ) : null}
        <Button pending={propose.isPending} onPress={() => propose.mutate(buildPack(f))}>Store this version</Button>
        {propose.error ? <RegError error={propose.error} /> : null}
        {propose.data && !propose.data.complete ? (
          <Banner tone="warn" title="Stored, and not complete">
            <ul className="list-disc pl-5">{propose.data.problems.map((c) => <li key={c}>{problemText(c)}</li>)}</ul>
          </Banner>
        ) : null}
      </div>
    </Card>
  );
}
