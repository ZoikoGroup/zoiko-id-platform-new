"use client";

import type { IssuerTrustItem, TrustIn, TrustStatusIn } from "@zoiko/api-client";
import { Button, Card, CopyId, DataTable, DateTime, Dialog, EmptyState, RadioCards, RevisionChip, Select, StatusBadge, TextField, humanize, type Column } from "@zoiko/ui";
import { Plus, ShieldCheck } from "lucide-react";
import { useState } from "react";

import { jsonInput, listInput, useCellMutation, useCellQuery } from "@/lib/cell";

import { ANCHORS_KEY } from "../identity-providers/integration-form";
import {
  AdminQueryView,
  AlgorithmPicker,
  FormDialog,
  R3ActionDialog,
  certificatesInput,
  isoFromLocal,
  joined,
  localDateTimeFromNow,
  required,
  type Alg,
} from "../identity-providers/shared";

export const TRUST_KEY = ["admin", "credentials", "issuer-trust"];

type Source = TrustIn["trust_source"];
type TrustStatus = TrustStatusIn["status"];

const STATUS_COPY: Record<TrustStatus, string> = {
  ACTIVE: "Credentials from this issuer are accepted again in presentations.",
  SUSPENDED: "Presentations of this issuer's credentials are refused until it is set active again.",
  QUARANTINED: "Treat the issuer as possibly compromised: its credentials are refused while you investigate.",
  REVOKED: "The issuer is no longer trusted. Its credentials are refused from now on.",
};

function AddTrustDialog({ onClose }: { onClose: () => void }) {
  const anchors = useCellQuery(ANCHORS_KEY, (api) => api.federation.trustAnchors());
  const [issuer, setIssuer] = useState("");
  const [types, setTypes] = useState("");
  const [jurisdictions, setJurisdictions] = useState("");
  const [source, setSource] = useState<Source>("PINNED");
  const [jwks, setJwks] = useState("");
  const [iaca, setIaca] = useState("");
  const [anchorId, setAnchorId] = useState<string | null>(null);
  const [algorithms, setAlgorithms] = useState<Alg[]>(["ES256"]);
  const [validTo, setValidTo] = useState(() => localDateTimeFromNow(365));
  const [localError, setLocalError] = useState<unknown>(null);

  const create = useCellMutation((api, body: TrustIn) => api.credentials.createIssuerTrust(body), {
    invalidate: [TRUST_KEY],
    success: (r) => `${r.issuer_identifier} is now trusted`,
    onSuccess: onClose,
  });

  function submit() {
    setLocalError(null);
    create.reset();
    try {
      const t = listInput(types);
      if (!t.length) throw new Error("Add at least one credential type.");
      if (t.length > 20) throw new Error("At most 20 credential types.");
      if (!algorithms.length) throw new Error("Choose at least one algorithm.");
      const body: TrustIn = {
        issuer_identifier: required(issuer, "Issuer identifier"),
        credential_types: t,
        jurisdictions: listInput(jurisdictions).map((j) => j.toUpperCase()),
        trust_source: source,
        algorithms,
        valid_to: isoFromLocal(validTo, "Trusted until"),
      };
      if (source === "PINNED") {
        const keys = jsonInput(jwks, "JWKS");
        const certs = certificatesInput(iaca);
        if (!keys && !certs.length) throw new Error("Pin a JWKS or at least one IACA certificate (for mdoc).");
        if (keys) body.jwks = keys;
        if (certs.length) body.iaca_certificates = certs;
      } else {
        if (!anchorId) throw new Error("Choose the trust anchor that vouches for this issuer.");
        body.trust_anchor_id = anchorId;
      }
      create.mutate(body);
    } catch (e) {
      setLocalError(e);
    }
  }

  const anchorOptions = (anchors.data?.items ?? [])
    .filter((a) => a.status === "ACTIVE")
    .map((a) => ({ id: a.anchor_id, label: a.entity_id, description: a.allowed_domains.join(", ") }));

  return (
    <FormDialog
      isOpen
      onClose={onClose}
      title="Trust a credential issuer"
      description="Credentials presented to this tenant are accepted only from issuers trusted here, for the listed types."
      submitLabel="Add issuer"
      pending={create.isPending}
      error={localError ?? create.error}
      onSubmit={submit}
      risk="R3"
    >
      <div className="grid gap-4 sm:grid-cols-2">
        <TextField
          label="Issuer identifier"
          isRequired
          mono
          value={issuer}
          onChange={setIssuer}
          placeholder="https://issuer.example.com"
          className="sm:col-span-2"
        />
        <TextField label="Credential types" isRequired mono value={types} onChange={setTypes} description="Comma separated, up to 20." />
        <TextField label="Jurisdictions (optional)" mono value={jurisdictions} onChange={setJurisdictions} placeholder="GB, IE" />
        <TextField label="Trusted until" type="datetime-local" isRequired value={validTo} onChange={setValidTo} />
      </div>
      <RadioCards
        label="Trust source"
        columns={2}
        value={source}
        onChange={(v) => setSource(v as Source)}
        options={[
          { id: "PINNED", label: "Pinned keys", description: "Enter the issuer's JWKS or IACA certificates." },
          { id: "FEDERATION", label: "OpenID Federation", description: "Resolve the issuer through a trust anchor." },
        ]}
      />
      {source === "PINNED" ? (
        <div className="grid gap-4 sm:grid-cols-2">
          <TextField label="JWKS" multiline rows={5} mono value={jwks} onChange={setJwks} placeholder='{"keys": [ … ]}' description="For SD-JWT VC and VC-JWT issuers." />
          <TextField
            label="IACA certificates"
            multiline
            rows={5}
            mono
            value={iaca}
            onChange={setIaca}
            placeholder="-----BEGIN CERTIFICATE-----"
            description="For mdoc issuers. PEM blocks or one base64 DER per line."
          />
        </div>
      ) : (
        <Select
          label="Trust anchor"
          isRequired
          value={anchorId}
          onChange={setAnchorId}
          options={anchorOptions}
          placeholder={anchors.isPending ? "Loading…" : anchorOptions.length ? "Select…" : "No active trust anchors"}
          description="Trust anchors are managed under Identity providers."
        />
      )}
      <AlgorithmPicker legend="Accepted algorithms" value={algorithms} onChange={setAlgorithms} />
    </FormDialog>
  );
}

function StatusChangeDialog({ row, onClose }: { row: IssuerTrustItem; onClose: () => void }) {
  const [choice, setChoice] = useState<TrustStatus | null>(null);
  const [status, setStatus] = useState<TrustStatus | null>(null);
  const change = useCellMutation((api, body: TrustStatusIn) => api.credentials.setIssuerTrustStatus(body), {
    invalidate: [TRUST_KEY],
    success: (r) => `${r.issuer_identifier} is now ${humanize(r.status).toLowerCase()}`,
  });

  if (!status) {
    const options = (["ACTIVE", "SUSPENDED", "QUARANTINED", "REVOKED"] as TrustStatus[]).filter((s) => s !== row.status);
    return (
      <Dialog
        isOpen
        onOpenChange={(o) => !o && onClose()}
        title="Change issuer trust"
        description={<span className="break-all font-mono text-xs">{row.issuer_identifier}</span>}
        size="md"
        footer={
          <>
            <Button variant="secondary" onPress={onClose}>
              Cancel
            </Button>
            <Button isDisabled={!choice} onPress={() => setStatus(choice)}>
              Review change
            </Button>
          </>
        }
      >
        <RadioCards
          label="New status"
          value={choice}
          onChange={(v) => setChoice(v as TrustStatus)}
          options={options.map((s) => ({ id: s, label: humanize(s), description: STATUS_COPY[s] }))}
        />
      </Dialog>
    );
  }

  return (
    <R3ActionDialog
      isOpen
      onClose={onClose}
      title={`Set issuer trust to ${humanize(status).toLowerCase()}`}
      intent={<p>{STATUS_COPY[status]}</p>}
      scope={[
        ["Issuer", <span key="i" className="break-all font-mono text-xs">{row.issuer_identifier}</span>],
        ["Current status", <StatusBadge key="c" state={row.status} kind="Issuer trust" />],
        ["New status", <StatusBadge key="n" state={status} kind="Issuer trust" />],
        ["Credential types", row.credential_types.join(", ")],
      ]}
      reversibility={status === "ACTIVE" ? "reversible" : "reversible-with-effort"}
      dependents={status === "ACTIVE" ? undefined : [{ label: "Presentations of credentials from this issuer", effect: "Refused" }]}
      commitLabel="Change status"
      run={async () => {
        const r = await change.mutateAsync({ issuer_identifier: row.issuer_identifier, status });
        return { outcome: r.status, objectId: row.trust_id };
      }}
    />
  );
}

export function IssuerTrustSection() {
  const trust = useCellQuery(TRUST_KEY, (api) => api.credentials.issuerTrust());
  const [adding, setAdding] = useState(false);
  const [target, setTarget] = useState<IssuerTrustItem | null>(null);

  const columns: Column<IssuerTrustItem>[] = [
    {
      key: "issuer",
      header: "Issuer",
      pinned: true,
      sortValue: (t) => t.issuer_identifier,
      render: (t) => (
        <div className="min-w-0">
          <div className="max-w-72 truncate font-mono text-xs font-medium text-ink" title={t.issuer_identifier}>
            {t.issuer_identifier}
          </div>
          <CopyId value={t.trust_id} label="issuer trust" />
        </div>
      ),
    },
    { key: "types", header: "Credential types", render: (t) => joined(t.credential_types) },
    { key: "source", header: "Source", sortValue: (t) => t.trust_source, render: (t) => <span className="text-xs">{humanize(t.trust_source)}</span> },
    { key: "status", header: "Status", sortValue: (t) => t.status, render: (t) => <StatusBadge state={t.status} kind="Issuer trust" /> },
    { key: "valid", header: "Trusted until", sortValue: (t) => t.valid_to, render: (t) => <DateTime value={t.valid_to} /> },
    { key: "jurisdictions", header: "Jurisdictions", defaultHidden: true, render: (t) => joined(t.jurisdictions) },
    { key: "algs", header: "Algorithms", defaultHidden: true, render: (t) => joined(t.algorithms) },
    { key: "revision", header: "Revision", defaultHidden: true, render: (t) => <RevisionChip kind="rev" value={t.revision} /> },
    {
      key: "actions",
      header: "Actions",
      align: "end",
      render: (t) => (
        <Button size="xs" variant="secondary" onPress={() => setTarget(t)}>
          Change status
        </Button>
      ),
    },
  ];

  return (
    <>
      <Card
        padded={false}
        title="Issuer trust"
        description="Issuers whose credentials this tenant accepts in presentations."
        actions={
          <Button size="sm" icon={<Plus aria-hidden className="size-4" />} onPress={() => setAdding(true)}>
            Add issuer
          </Button>
        }
      >
        <AdminQueryView query={trust}>
          {(data) => (
            <DataTable
              caption="Trusted credential issuers"
              columns={columns}
              rows={data.items}
              rowKey={(t) => t.trust_id}
              empty={
                <EmptyState icon={ShieldCheck} title="No trusted issuers">
                  Presentations are refused until at least one issuer is trusted.
                </EmptyState>
              }
            />
          )}
        </AdminQueryView>
      </Card>

      {adding ? <AddTrustDialog onClose={() => setAdding(false)} /> : null}
      {target ? <StatusChangeDialog row={target} onClose={() => setTarget(null)} /> : null}
    </>
  );
}
