"use client";

import type { BillingProfile, BillingProfileVersion } from "@zoiko/api-client";
import { Banner, Button, Card, DataTable, EmptyState, KeyValues, Select, TextField, type Column } from "@zoiko/ui";
import { History, Plus, Trash2 } from "lucide-react";
import { useState, type FormEvent } from "react";

import { ApiErrorView, QueryView, useCellMutation, useCellQuery } from "@/lib/cell";

import { BILLING_KEY } from "./shared";
import {
  MAX_TAX_IDS,
  TAX_ID_TYPES,
  formFromProfile,
  profileBody,
  profileChanged,
  profileFieldLabel,
  validateProfile,
  type ProfileForm,
} from "./profile-logic";

const PROFILE_KEY = [...BILLING_KEY, "profile"] as const;

/** B04. The payer's address, tax identifiers and billing contacts. The legal payer and tax country are shown, never edited here: they
 * change through a reviewed request. Nothing the person types is marked verified. */
export function ProfileSection({ canEdit, canSeeHistory }: { canEdit: boolean; canSeeHistory: boolean }) {
  const profile = useCellQuery(PROFILE_KEY, (api) => api.billing.profile());
  return (
    <QueryView query={profile}>
      {(p) => (
        <div className="space-y-4">
          <Payer p={p} />
          {/* Keyed by version so the form starts from what was just saved, or from the newer version after a conflict. */}
          <ProfileForm key={p.version} p={p} canEdit={canEdit} />
          {canSeeHistory ? <ProfileHistory /> : null}
        </div>
      )}
    </QueryView>
  );
}

function Payer({ p }: { p: BillingProfile }) {
  return (
    <Card title="Legal payer" description="Set when your plan was ordered. A change goes through a reviewed request, not this page.">
      <KeyValues
        columns={3}
        items={[
          ["Legal payer", p.payer.legal_name ?? "Not set up"],
          ["Country", p.payer.country ?? "—"],
          ["Currency", p.payer.currency ?? "—"],
        ]}
      />
      <p className="mt-3 text-xs text-ink-subtle">{p.payer.change_note}</p>
      <p className="mt-2 text-sm">
        <a className="font-medium text-brand-700 underline" href="/help?topic=billing-payer-change">
          Ask Zoiko to review a change of legal payer or tax country
        </a>
      </p>
    </Card>
  );
}

function ProfileForm({ p, canEdit }: { p: BillingProfile; canEdit: boolean }) {
  const saved = formFromProfile(p);
  const [f, setF] = useState<ProfileForm>(saved);
  const [shown, setShown] = useState(false);
  const errors = validateProfile(f);
  const invalid = Object.keys(errors).length > 0;
  const changed = profileChanged(f, saved);
  const set = <K extends keyof ProfileForm>(k: K, v: ProfileForm[K]) => setF((cur) => ({ ...cur, [k]: v }));

  const save = useCellMutation((api, form: ProfileForm) => api.billing.saveProfile(profileBody(form, p.version)), {
    invalidate: [PROFILE_KEY],
    success: "Billing profile saved.",
  });

  const submit = (e: FormEvent) => {
    e.preventDefault();
    setShown(true);
    if (!invalid && changed && canEdit) save.mutate(f);
  };
  const err = (k: keyof typeof errors) => (shown ? errors[k] : undefined);
  const off = !canEdit;

  return (
    <form onSubmit={submit} className="space-y-4" noValidate>
      {off ? (
        <Banner tone="info" title="You can read the billing profile but not change it">
          Changing it needs the Billing Admin access an Owner can give you.
        </Banner>
      ) : null}
      <Card title="Billing address" description="Printed on invoices.">
        <div className="grid gap-3 sm:grid-cols-2">
          <TextField label="Address line 1" value={f.line1} onChange={(v) => set("line1", v)} errorMessage={err("line1")} isDisabled={off} isRequired />
          <TextField label="Address line 2" value={f.line2} onChange={(v) => set("line2", v)} isDisabled={off} />
          <TextField label="City" value={f.city} onChange={(v) => set("city", v)} errorMessage={err("city")} isDisabled={off} isRequired />
          <TextField label="Region or state" value={f.region} onChange={(v) => set("region", v)} isDisabled={off} />
          <TextField label="Postal code" value={f.postal_code} onChange={(v) => set("postal_code", v)} isDisabled={off} />
          <TextField
            label="Country (two letters)"
            value={f.country}
            onChange={(v) => set("country", v.toUpperCase().slice(0, 2))}
            errorMessage={err("country")}
            isDisabled={off}
            isRequired
          />
        </div>
      </Card>

      <Card
        title="Tax identifiers"
        description="Saved as you enter them. They are not checked against a register, so they show as unverified."
      >
        <ul className="space-y-3">
          {f.tax_ids.map((t, i) => (
            <li key={i} className="flex flex-wrap items-end gap-3">
              <Select
                label="Type"
                options={TAX_ID_TYPES}
                value={t.type}
                onChange={(v) => set("tax_ids", f.tax_ids.map((x, j) => (j === i ? { ...x, type: v as typeof t.type } : x)))}
                isDisabled={off}
                className="min-w-56"
              />
              <TextField
                label="Number"
                value={t.value}
                onChange={(v) => set("tax_ids", f.tax_ids.map((x, j) => (j === i ? { ...x, value: v } : x)))}
                isDisabled={off}
                className="min-w-56"
                mono
              />
              <span className="pb-2 text-xs text-ink-subtle">Unverified</span>
              {canEdit ? (
                <Button variant="secondary" icon={<Trash2 aria-hidden className="size-4" />} onPress={() => set("tax_ids", f.tax_ids.filter((_, j) => j !== i))}>
                  Remove
                </Button>
              ) : null}
            </li>
          ))}
        </ul>
        {f.tax_ids.length === 0 ? <p className="text-sm text-ink-muted">No tax identifier saved.</p> : null}
        {shown && errors.tax_ids ? <p className="mt-2 text-xs font-medium text-danger-fg">{errors.tax_ids}</p> : null}
        {canEdit ? (
          <div className="mt-3">
            <Button
              variant="secondary"
              icon={<Plus aria-hidden className="size-4" />}
              isDisabled={f.tax_ids.length >= MAX_TAX_IDS}
              onPress={() => set("tax_ids", [...f.tax_ids, { type: "VAT", value: "" }])}
            >
              Add a tax identifier
            </Button>
          </div>
        ) : null}
      </Card>

      <Card title="Billing contacts" description="Where billing mail goes. Addresses are not confirmed by a message, so they show as unverified.">
        <div className="grid gap-3 sm:grid-cols-3">
          <TextField label="Invoice email" type="email" value={f.invoice_email} onChange={(v) => set("invoice_email", v)} errorMessage={err("invoice_email")} isDisabled={off} isRequired />
          <TextField label="Notices email" type="email" value={f.notices_email} onChange={(v) => set("notices_email", v)} errorMessage={err("notices_email")} isDisabled={off} />
          <TextField label="Tax document email" type="email" value={f.tax_document_email} onChange={(v) => set("tax_document_email", v)} errorMessage={err("tax_document_email")} isDisabled={off} />
        </div>
      </Card>

      {canEdit ? (
        <div className="flex flex-wrap items-center gap-3">
          <Button type="submit" isDisabled={save.isPending || !changed}>
            Save billing profile
          </Button>
          <Button variant="secondary" isDisabled={!changed || save.isPending} onPress={() => { setF(saved); setShown(false); save.reset(); }}>
            Discard changes
          </Button>
          <span className="text-xs text-ink-subtle">Version {p.version}{p.updated_at ? ` · saved ${new Date(p.updated_at).toLocaleDateString("en-GB")}` : ""}</span>
        </div>
      ) : null}
      {save.isError ? <ApiErrorView error={save.error} title="The billing profile was not saved" onRetry={() => save.reset()} /> : null}
    </form>
  );
}

function ProfileHistory() {
  const history = useCellQuery([...PROFILE_KEY, "history"], (api) => api.billing.profileHistory());
  const columns: Column<BillingProfileVersion>[] = [
    { key: "v", header: "Version", pinned: true, render: (h) => h.version, sortValue: (h) => h.version },
    { key: "fields", header: "What changed", render: (h) => (h.changed_fields.length ? h.changed_fields.map(profileFieldLabel).join(", ") : "First save") },
    { key: "by", header: "By", render: (h) => <span className="font-mono text-xs">{h.changed_by.slice(0, 8)}</span> },
    { key: "at", header: "When", render: (h) => new Date(h.changed_at).toLocaleString("en-GB"), sortValue: (h) => h.changed_at },
  ];
  return (
    <QueryView query={history}>
      {(data) => (
        <Card title="Change history" description="Every saved version stays on record. Nothing is overwritten." padded={false}>
          <DataTable<BillingProfileVersion>
            caption="Billing profile versions"
            columns={columns}
            rows={data.items}
            rowKey={(h) => String(h.version)}
            empty={<EmptyState title="No saved versions yet" icon={History}>The first save appears here.</EmptyState>}
          />
        </Card>
      )}
    </QueryView>
  );
}
