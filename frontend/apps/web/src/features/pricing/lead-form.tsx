"use client";

import type { PublicLeadOut, PublicPricingPage } from "@zoiko/api-client";
import { Banner, Button, Card, RadioCards, Select, TextField } from "@zoiko/ui";
import { useState, type FormEvent } from "react";

import { EMPTY_LEAD, PARTNER_TYPES, VOLUME_BANDS, buildLead, cleanCode, countryOptions, leadErrors, type LeadDraft } from "./logic";

const COUNTRIES = countryOptions();

/** The enterprise and partner inquiry (WF-P07). It asks for the least Sales needs to reply, refuses identity numbers and links, and says plainly that sending it creates no
 * account, quote, contract or payment. The hidden field is for robots: a person never reaches it. */
export function LeadForm({ capabilities, names, kind, onKind, notice, lockKind = false }: { capabilities: string[]; names: Record<string, string>; kind: LeadDraft["kind"]; onKind: (k: LeadDraft["kind"]) => void; notice: PublicPricingPage["notice"]; lockKind?: boolean }) {
  const [draft, setDraft] = useState<LeadDraft>(EMPTY_LEAD);
  const [shown, setShown] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [done, setDone] = useState<PublicLeadOut | null>(null);
  const d: LeadDraft = { ...draft, kind };
  const set = <K extends keyof LeadDraft>(key: K, value: LeadDraft[K]) => setDraft((s) => ({ ...s, [key]: value }));

  async function submit(e: FormEvent) {
    e.preventDefault();
    const errors = leadErrors(d);
    setShown(errors);
    setProblem(null);
    const body = buildLead(d, capabilities, notice.version);
    if (!body) return;
    setBusy(true);
    try {
      const res = await fetch("/api/pricing/leads", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
      const payload = (await res.json()) as PublicLeadOut & { error?: { message?: string } };
      if (!res.ok) throw new Error(payload.error?.message ?? "Your inquiry could not be sent.");
      setDone(payload);
    } catch (err) {
      setProblem(err instanceof Error ? err.message : "Your inquiry could not be sent.");
    } finally {
      setBusy(false);
    }
  }

  if (done) {
    return (
      <Card title="Thank you">
        <div role="status" className="space-y-2 text-sm">
          <p>{done.message}</p>
          <p className="text-ink-muted">
            Reference <span className="font-mono">{done.reference}</span>
            {done.duplicate ? ". You already have an open inquiry, so this one was not duplicated." : "."}
          </p>
        </div>
      </Card>
    );
  }

  const errorList = Object.entries(shown);
  return (
    <Card title={kind === "PARTNER" ? "Partner inquiry" : "Request an enterprise quote"} description="Tell us what you need and Sales will reply. Nothing is bought or switched on by sending this.">
      <form onSubmit={submit} noValidate className="space-y-4" aria-describedby="lead-notice">
        {lockKind ? null : (
        <RadioCards
          label="What are you asking about?"
          value={kind}
          onChange={(v) => onKind(v as LeadDraft["kind"])}
          columns={2}
          options={[
            { id: "ENTERPRISE", label: "An enterprise solution", description: "Scale, regions, controls and a signed order." },
            { id: "PARTNER", label: "A partner arrangement", description: "Reseller, referral or embedded. Agreed by contract; never general checkout." },
          ]}
        />
        )}
        {errorList.length ? (
          <div role="alert" className="rounded-control border border-danger-fg/40 bg-danger-bg p-3 text-sm">
            <p className="font-medium">Please fix {errorList.length === 1 ? "this" : "these"} before sending:</p>
            <ul className="mt-1 list-disc pl-5">
              {errorList.map(([k, m]) => (
                <li key={k}>{m}</li>
              ))}
            </ul>
          </div>
        ) : null}
        <div className="grid gap-4 sm:grid-cols-2">
          <TextField label="Your name" value={d.name} onChange={(v) => set("name", v)} autoComplete="name" errorMessage={shown.name} isRequired />
          <TextField label="Work email" type="email" value={d.email} onChange={(v) => set("email", v)} autoComplete="email" errorMessage={shown.email} isRequired />
          <TextField label="Company" value={d.company} onChange={(v) => set("company", v)} autoComplete="organization" errorMessage={shown.company} isRequired />
          <Select label="Country of your company" options={COUNTRIES} value={d.country ? cleanCode(d.country) : null} onChange={(v) => set("country", v)} errorMessage={shown.country} isRequired placeholder="Choose a country" />
          <Select label="Expected volume" options={VOLUME_BANDS} value={d.volume || null} onChange={(v) => set("volume", v as LeadDraft["volume"])} errorMessage={shown.volume} isRequired placeholder="Choose a range" />
          <TextField label="Legal entities involved" description="Optional." inputMode="numeric" value={d.entities} onChange={(v) => set("entities", v)} errorMessage={shown.entities} />
          {kind === "PARTNER" ? (
            <Select label="Kind of partner" options={PARTNER_TYPES} value={d.partnerType || null} onChange={(v) => set("partnerType", v as LeadDraft["partnerType"])} errorMessage={shown.partnerType} isRequired placeholder="Choose one" />
          ) : null}
          <TextField label="Data region you need" description="Optional, for example eu-central." value={d.region} onChange={(v) => set("region", v)} errorMessage={shown.region} />
        </div>
        <TextField label="Security or procurement needs" description="Optional. Please do not include identity documents or numbers." multiline rows={3} value={d.needs} onChange={(v) => set("needs", v)} errorMessage={shown.needs} />
        <TextField label="Where did you hear about us?" description="Optional." value={d.referral} onChange={(v) => set("referral", v)} errorMessage={shown.referral} />
        {capabilities.length ? (
          <p className="text-sm">
            <span className="font-medium">Services you asked about:</span> {capabilities.map((c) => names[c] ?? c).join(", ")}.
          </p>
        ) : null}
        <div aria-hidden className="absolute -left-[9999px] h-0 w-0 overflow-hidden">
          <label>
            Leave this field empty
            <input type="text" name="website" tabIndex={-1} autoComplete="off" value={d.website} onChange={(e) => set("website", e.target.value)} />
          </label>
        </div>
        <p id="lead-notice" className="text-xs text-ink-muted">
          {notice.text} Sending this does not create an account, a quote, a contract or a payment, and you will not be charged. Please do not upload or type identity documents, biometric data or document numbers.
        </p>
        <p className="text-xs text-ink-muted">
          Privacy notice version <span className="font-mono">{notice.version}</span>
          {notice.status === "DRAFT" ? ". This notice is a draft pending legal approval." : "."}
        </p>
        {problem ? (
          <Banner tone="danger" live="assertive">
            {problem}
          </Banner>
        ) : null}
        <Button type="submit" isDisabled={busy} pending={busy}>
          {kind === "PARTNER" ? "Send partner inquiry" : "Request enterprise quote"}
        </Button>
      </form>
    </Card>
  );
}
