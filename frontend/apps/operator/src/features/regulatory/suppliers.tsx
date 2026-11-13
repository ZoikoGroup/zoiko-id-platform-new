"use client";

import type { AttestationRow, CertStatus, EntitlementIn, EntitlementRow, RegisterSource } from "@zoiko/api-client";
import { Badge, Button, Card, Checkbox, DataTable, DateTime, Dialog, EmptyState, PageHeader, Select, TextField, type Column } from "@zoiko/ui";
import { BadgeCheck, FileSignature } from "lucide-react";
import { useState } from "react";

import { QueryView, useCellMutation, useCellQuery } from "@/lib/op";

import { CAPABILITY_OPTIONS, COUNTRY_OPTIONS, PURPOSE_OPTIONS, SCHEME_OPTIONS, isoFromLocal, Mono, opt, REG, Reasons, RegError, upper, Verdict } from "./common";

const CERT: CertStatus[] = ["ACTIVE", "SUSPENDED", "WITHDRAWN", "EXPIRED"];
const ENT: EntitlementIn["status"][] = ["GRANTED", "SUSPENDED", "WITHDRAWN"];

function AttestationDialog({ isOpen, onClose }: { isOpen: boolean; onClose: () => void }) {
  const blank = {
    entity: "",
    scheme: "UK_DIATF",
    country: "",
    service_code: "",
    scheme_version: "",
    certificate_ref: "",
    status: "ACTIVE" as CertStatus,
    valid_from: "",
    valid_until: "",
    register_url: "",
    register_entry_id: "",
    cab: "",
    fetched_at: "",
    proof_hash: "",
    evidence_locator: "",
  };
  const [f, setF] = useState(blank);
  const [componentOnly, setComponentOnly] = useState(false);
  const set = (k: keyof typeof blank) => (v: string) => setF((p) => ({ ...p, [k]: v }));
  const save = useCellMutation(
    (api) =>
      api.regulatory.recordAttestation({
        entity: f.entity.trim(),
        scheme: f.scheme.trim(),
        country: upper(f.country),
        service_code: f.service_code.trim(),
        scheme_version: f.scheme_version.trim(),
        certificate_ref: f.certificate_ref.trim(),
        component_only: componentOnly,
        status: f.status,
        valid_from: f.valid_from,
        valid_until: opt(f.valid_until) ?? null,
        register_url: f.register_url.trim(),
        register_entry_id: f.register_entry_id.trim(),
        cab: opt(f.cab) ?? null,
        fetched_at: isoFromLocal(f.fetched_at),
        proof_hash: f.proof_hash.trim().toLowerCase(),
        evidence_locator: f.evidence_locator.trim(),
      }),
    {
      invalidate: [REG.attestations, REG.readiness],
      success: "Register snapshot recorded",
      onSuccess: () => {
        setF(blank);
        onClose();
      },
    },
  );
  const ready =
    f.entity && f.country.trim().length === 2 && f.service_code && f.register_url.startsWith("https://") && f.valid_from && f.fetched_at && /^[0-9a-fA-F]{64}$/.test(f.proof_hash.trim());
  return (
    <Dialog
      isOpen={isOpen}
      onOpenChange={(o) => !o && onClose()}
      title="Record what the official register says"
      description="Copy the register entry as it stands today. Only the official register counts; a provider's own claim does not. Records are append-only."
      size="lg"
      footer={
        <>
          <Button variant="secondary" onPress={onClose}>
            Cancel
          </Button>
          <Button onPress={() => save.mutate(undefined)} pending={save.isPending} isDisabled={!ready}>
            Record
          </Button>
        </>
      }
    >
      <div className="grid gap-3 sm:grid-cols-2">
        <TextField label="Provider (entity)" description="the supplier's own id" placeholder="veriff" value={f.entity} onChange={set("entity")} mono isRequired />
        <Select label="Scheme" options={SCHEME_OPTIONS} value={f.scheme} onChange={set("scheme")} isRequired />
        <Select label="Country" options={COUNTRY_OPTIONS} value={f.country} onChange={set("country")} isRequired />
        <TextField label="Service code" description="the certified service" placeholder="IDV" value={f.service_code} onChange={set("service_code")} mono isRequired />
        <TextField label="Scheme version" placeholder="0.4" value={f.scheme_version} onChange={set("scheme_version")} isRequired />
        <TextField label="Certificate reference" placeholder="DIATF-2026-0142" value={f.certificate_ref} onChange={set("certificate_ref")} isRequired />
        <Select label="Register status" value={f.status} onChange={(v) => setF((p) => ({ ...p, status: CERT.find((c) => c === v) ?? "ACTIVE" }))} options={CERT.map((c) => ({ id: c, label: c }))} />
        <TextField label="Certifying body" placeholder="BSI Assurance UK" value={f.cab} onChange={set("cab")} />
        <TextField label="Valid from" type="date" value={f.valid_from} onChange={set("valid_from")} isRequired />
        <TextField label="Valid until" type="date" value={f.valid_until} onChange={set("valid_until")} />
        <TextField label="Register URL" description="https only" placeholder="https://register.example.gov/entry/142" type="url" value={f.register_url} onChange={set("register_url")} isRequired />
        <TextField label="Register entry ID" placeholder="142" value={f.register_entry_id} onChange={set("register_entry_id")} isRequired />
        <TextField label="Fetched at" type="datetime-local" value={f.fetched_at} onChange={set("fetched_at")} isRequired />
        <TextField label="Proof hash" description="SHA-256 of the saved register page" value={f.proof_hash} onChange={set("proof_hash")} mono isRequired />
        <TextField label="Evidence locator" description="where the saved copy is kept" placeholder="s3://evidence/registers/142.html" value={f.evidence_locator} onChange={set("evidence_locator")} isRequired />
        <Checkbox isSelected={componentOnly} onChange={setComponentOnly} description="The certificate covers a component, not the whole service">
          Component only
        </Checkbox>
      </div>
      {save.error ? <RegError error={save.error} /> : null}
    </Dialog>
  );
}

function EntitlementDialog({ isOpen, onClose }: { isOpen: boolean; onClose: () => void }) {
  const blank = { provider: "", country: "", capability: "INDIVIDUAL_VERIFICATION", purpose: "ENGAGEMENT", status: "GRANTED" as EntitlementIn["status"], effective_at: "", valid_from: "", valid_until: "", contract_ref: "", terms_ref: "" };
  const [f, setF] = useState(blank);
  const set = (k: keyof typeof blank) => (v: string) => setF((p) => ({ ...p, [k]: v }));
  const save = useCellMutation(
    (api) =>
      api.regulatory.recordEntitlement({
        provider: f.provider.trim(),
        country: upper(f.country),
        capability: upper(f.capability),
        purpose: upper(f.purpose),
        status: f.status,
        effective_at: isoFromLocal(f.effective_at),
        valid_from: isoFromLocal(f.valid_from),
        valid_until: f.valid_until ? isoFromLocal(f.valid_until) : null,
        contract_ref: f.contract_ref.trim(),
        terms_ref: f.terms_ref.trim(),
      }),
    {
      invalidate: [REG.entitlements, REG.readiness],
      success: "Entitlement recorded",
      onSuccess: () => {
        setF(blank);
        onClose();
      },
    },
  );
  const ready = f.provider && f.country.trim().length === 2 && f.effective_at && f.valid_from && f.contract_ref.trim().length >= 3 && f.terms_ref.trim().length >= 3;
  return (
    <Dialog
      isOpen={isOpen}
      onOpenChange={(o) => !o && onClose()}
      title="Record a provider entitlement"
      description="What our contract with the provider lets us use them for, in one country, capability and purpose. Nothing is inferred from other scopes."
      size="lg"
      footer={
        <>
          <Button variant="secondary" onPress={onClose}>
            Cancel
          </Button>
          <Button onPress={() => save.mutate(undefined)} pending={save.isPending} isDisabled={!ready}>
            Record
          </Button>
        </>
      }
    >
      <div className="grid gap-3 sm:grid-cols-2">
        <TextField label="Provider" description="the supplier's own id" placeholder="veriff" value={f.provider} onChange={set("provider")} mono isRequired />
        <Select label="Country" options={COUNTRY_OPTIONS} value={f.country} onChange={set("country")} isRequired />
        <Select label="Capability" options={CAPABILITY_OPTIONS} value={f.capability} onChange={set("capability")} isRequired />
        <Select label="Purpose" options={PURPOSE_OPTIONS} value={f.purpose} onChange={set("purpose")} isRequired />
        <Select label="Status" value={f.status} onChange={(v) => setF((p) => ({ ...p, status: ENT.find((s) => s === v) ?? "GRANTED" }))} options={ENT.map((s) => ({ id: s, label: s }))} />
        <TextField label="Effective at" type="datetime-local" value={f.effective_at} onChange={set("effective_at")} isRequired />
        <TextField label="Valid from" type="datetime-local" value={f.valid_from} onChange={set("valid_from")} isRequired />
        <TextField label="Valid until" type="datetime-local" value={f.valid_until} onChange={set("valid_until")} />
        <TextField label="Contract reference" placeholder="MSA-2026-017" value={f.contract_ref} onChange={set("contract_ref")} isRequired />
        <TextField label="Terms reference" placeholder="https://provider.example/terms/2026" value={f.terms_ref} onChange={set("terms_ref")} isRequired />
      </div>
      {save.error ? <RegError error={save.error} /> : null}
    </Dialog>
  );
}

function RegisterSources() {
  const q = useCellQuery([...REG.attestations, "sources"], (api) => api.regulatory.registerSources());
  const [f, setF] = useState({ source_id: "simulator", entity: "", scheme: "UK_DIATF", country: "GB", service_code: "" });
  const set = (k: keyof typeof f) => (v: string) => setF((p) => ({ ...p, [k]: v }));
  const refresh = useCellMutation(
    (api) =>
      api.regulatory.refreshFromRegister({
        source_id: f.source_id.trim(),
        entity: f.entity.trim(),
        scheme: f.scheme.trim(),
        country: upper(f.country),
        service_code: f.service_code.trim(),
      }),
    {
      invalidate: [REG.attestations, REG.readiness],
      success: (r) => (r.listed ? `Register answered: ${r.outcome}` : "The register does not list this scope: nothing recorded"),
    },
  );
  const columns: Column<RegisterSource>[] = [
    { key: "id", header: "Source", pinned: true, render: (s) => <Mono>{s.source_id}</Mono> },
    { key: "kind", header: "Kind", render: (s) => s.kind },
    { key: "scheme", header: "Scheme", render: (s) => <Mono>{s.scheme}</Mono> },
    { key: "usable", header: "Usable here", render: (s) => (s.usable ? <Badge tone="ok">Yes</Badge> : <Reasons reasons={s.reasons} />) },
  ];
  return (
    <Card padded={false} title="Official-register sources">
      <QueryView query={q}>
        {(d) => <DataTable caption="Register sources" columns={columns} rows={d.items} rowKey={(s) => s.source_id} />}
      </QueryView>
      <form
        aria-label="Refresh from register"
        className="flex flex-wrap items-end gap-2 border-t border-border p-4"
        onSubmit={(e) => {
          e.preventDefault();
          refresh.mutate(undefined);
        }}
      >
        <TextField label="Source" value={f.source_id} onChange={set("source_id")} mono />
        <TextField label="Provider (entity)" value={f.entity} onChange={set("entity")} mono isRequired />
        <TextField label="Scheme" value={f.scheme} onChange={set("scheme")} mono />
        <TextField label="Country" value={f.country} onChange={set("country")} className="w-20" />
        <TextField label="Service code" value={f.service_code} onChange={set("service_code")} mono isRequired />
        <Button type="submit" pending={refresh.isPending} isDisabled={!f.entity.trim() || !f.service_code.trim() || f.country.trim().length !== 2}>
          Ask the register
        </Button>
      </form>
      {refresh.error ? <div className="px-4 pb-4"><RegError error={refresh.error} /></div> : null}
    </Card>
  );
}

export function SuppliersPage() {
  const [provider, setProvider] = useState("");
  const att = useCellQuery([...REG.attestations, provider], (api) => api.regulatory.attestations(opt(provider)));
  const ent = useCellQuery([...REG.entitlements, provider], (api) => api.regulatory.entitlements(opt(provider)));
  const [recording, setRecording] = useState<"attestation" | "entitlement" | null>(null);
  const attCols: Column<AttestationRow>[] = [
    { key: "entity", header: "Provider", pinned: true, render: (a) => <Mono>{a.entity}</Mono>, sortValue: (a) => a.entity },
    { key: "scope", header: "Scope", render: (a) => `${a.scheme} · ${a.country} · ${a.service_code}` },
    { key: "today", header: "Today", render: (a) => <Verdict ok={a.outcome === "CERTIFIED"} yes="Certified" no="On hold" /> },
    { key: "reason", header: "Reason", render: (a) => <Mono>{a.reason}</Mono> },
    { key: "fetched", header: "Register read", render: (a) => (a.fetched_at ? <DateTime value={a.fetched_at} /> : "-") },
  ];
  const entCols: Column<EntitlementRow>[] = [
    { key: "provider", header: "Provider", pinned: true, render: (e) => <Mono>{e.provider}</Mono>, sortValue: (e) => e.provider },
    { key: "scope", header: "Scope", render: (e) => `${e.country} · ${e.capability} · ${e.purpose}` },
    { key: "today", header: "Today", render: (e) => <Verdict ok={e.outcome === "ENTITLED"} yes="Entitled" no="Denied" /> },
    { key: "reason", header: "Reason", render: (e) => <Mono>{e.reason}</Mono> },
    { key: "contract", header: "Contract", render: (e) => e.contract_ref ?? "-" },
    { key: "until", header: "Valid until", render: (e) => (e.valid_until ? <DateTime value={e.valid_until} /> : "open-ended") },
  ];
  return (
    <>
      <PageHeader
        title="Suppliers"
        description="Each provider's certification as the official register shows it (WF-03), and what our contract entitles us to use it for. A provider is used only where both hold."
        actions={
          <>
            <Button variant="secondary" onPress={() => setRecording("entitlement")}>
              Record entitlement
            </Button>
            <Button onPress={() => setRecording("attestation")}>Record register snapshot</Button>
          </>
        }
      />
      <div className="space-y-5">
        <TextField label="Provider" value={provider} onChange={setProvider} placeholder="All providers" className="max-w-xs" mono />
        <Card padded={false} title="Certifications (official registers)">
          <QueryView query={att}>
            {(d) => (
              <DataTable
                caption="Certifications"
                columns={attCols}
                rows={d.items}
                rowKey={(a) => `${a.entity}:${a.scheme}:${a.country}:${a.service_code}`}
                empty={<EmptyState icon={BadgeCheck} title="No register snapshots">No provider has a recorded certification, so none counts as certified.</EmptyState>}
              />
            )}
          </QueryView>
        </Card>
        <RegisterSources />
        <Card padded={false} title="Entitlements (contracts)">
          <QueryView query={ent}>
            {(d) => (
              <DataTable
                caption="Entitlements"
                columns={entCols}
                rows={d.items}
                rowKey={(e) => `${e.provider}:${e.country}:${e.capability}:${e.purpose}`}
                empty={<EmptyState icon={FileSignature} title="No entitlements">No provider is entitled anywhere yet.</EmptyState>}
              />
            )}
          </QueryView>
        </Card>
      </div>
      <AttestationDialog isOpen={recording === "attestation"} onClose={() => setRecording(null)} />
      <EntitlementDialog isOpen={recording === "entitlement"} onClose={() => setRecording(null)} />
    </>
  );
}
