"use client";

import type { RegistrationIn, RpRegistrationItem } from "@zoiko/api-client";
import { Button, Card, CopyId, DataTable, DateTime, EmptyState, StatusBadge, TextField, type Column } from "@zoiko/ui";
import { Building2, Plus } from "lucide-react";
import { useState } from "react";

import { listInput, useCellMutation, useCellQuery } from "@/lib/cell";

import { AdminQueryView, FormDialog, isoFromLocal, joined, localDateTimeFromNow, required } from "../identity-providers/shared";

export const RP_KEY = ["admin", "credentials", "relying-parties"];

function RegisterDialog({ onClose }: { onClose: () => void }) {
  const [clientId, setClientId] = useState("");
  const [purpose, setPurpose] = useState("");
  const [attributes, setAttributes] = useState("");
  const [retention, setRetention] = useState("0");
  const [validTo, setValidTo] = useState(() => localDateTimeFromNow(365));
  const [localError, setLocalError] = useState<unknown>(null);

  const register = useCellMutation((api, body: RegistrationIn) => api.credentials.registerRelyingParty(body), {
    invalidate: [RP_KEY],
    success: (r) => `${r.client_id} registered for ${r.purpose}`,
    onSuccess: onClose,
  });

  function submit() {
    setLocalError(null);
    register.reset();
    try {
      const attrs = listInput(attributes);
      if (!attrs.length) throw new Error("Add at least one allowed attribute.");
      if (attrs.length > 50) throw new Error("At most 50 allowed attributes.");
      const days = Number(retention);
      if (!Number.isInteger(days) || days < 0 || days > 3650) throw new Error("Retention must be 0–3650 days.");
      register.mutate({
        client_id: required(clientId, "Client ID"),
        purpose: required(purpose, "Purpose"),
        allowed_attributes: attrs,
        retention_days: days,
        valid_to: isoFromLocal(validTo, "Registered until"),
      });
    } catch (e) {
      setLocalError(e);
    }
  }

  return (
    <FormDialog
      isOpen
      onClose={onClose}
      title="Register relying party"
      description="Lets an OAuth client request presentations for one purpose, limited to the listed attributes."
      submitLabel="Register"
      pending={register.isPending}
      error={localError ?? register.error}
      onSubmit={submit}
      risk="R3"
      size="md"
    >
      <TextField label="Client ID" isRequired mono value={clientId} onChange={setClientId} description="The OAuth client that will request presentations." />
      <TextField label="Purpose" isRequired mono value={purpose} onChange={setPurpose} placeholder="age_verification" description="Shown to the holder; requests must name it." />
      <TextField
        label="Allowed attributes"
        isRequired
        mono
        value={attributes}
        onChange={setAttributes}
        placeholder="age_over_18, given_name"
        description="Comma separated, up to 50. Requests for anything else are refused."
      />
      <div className="grid gap-4 sm:grid-cols-2">
        <TextField
          label="Retention (days)"
          type="number"
          inputMode="numeric"
          value={retention}
          onChange={setRetention}
          description="How long the relying party may keep disclosed data. 0 means not retained."
        />
        <TextField label="Registered until" type="datetime-local" isRequired value={validTo} onChange={setValidTo} />
      </div>
    </FormDialog>
  );
}

export function RelyingPartiesSection() {
  const rps = useCellQuery(RP_KEY, (api) => api.credentials.relyingParties());
  const [registering, setRegistering] = useState(false);

  const columns: Column<RpRegistrationItem>[] = [
    {
      key: "client",
      header: "Client",
      pinned: true,
      sortValue: (r) => r.client_id,
      render: (r) => (
        <div className="min-w-0">
          <div className="font-mono text-xs font-medium text-ink">{r.client_id}</div>
          <CopyId value={r.registration_id} label="registration" />
        </div>
      ),
    },
    { key: "purpose", header: "Purpose", sortValue: (r) => r.purpose, render: (r) => <span className="font-mono text-xs">{r.purpose}</span> },
    { key: "attributes", header: "Allowed attributes", render: (r) => joined(r.allowed_attributes) },
    {
      key: "retention",
      header: "Retention",
      sortValue: (r) => r.retention_days,
      render: (r) => <span className="text-xs">{r.retention_days === 0 ? "Not retained" : `${r.retention_days} days`}</span>,
    },
    { key: "status", header: "Status", sortValue: (r) => r.status, render: (r) => <StatusBadge state={r.status} kind="Registration" /> },
    { key: "valid", header: "Valid until", sortValue: (r) => r.valid_to, render: (r) => <DateTime value={r.valid_to} /> },
  ];

  return (
    <>
      <Card
        padded={false}
        title="Relying-party registrations"
        description="Which clients may request presentations, for which purpose and attributes."
        actions={
          <Button size="sm" icon={<Plus aria-hidden className="size-4" />} onPress={() => setRegistering(true)}>
            Register relying party
          </Button>
        }
      >
        <AdminQueryView query={rps}>
          {(data) => (
            <DataTable
              caption="Relying-party registrations"
              columns={columns}
              rows={data.items}
              rowKey={(r) => r.registration_id}
              empty={
                <EmptyState icon={Building2} title="No relying parties registered">
                  A client must be registered for a purpose before it can request presentations.
                </EmptyState>
              }
            />
          )}
        </AdminQueryView>
      </Card>
      {registering ? <RegisterDialog onClose={() => setRegistering(false)} /> : null}
    </>
  );
}
