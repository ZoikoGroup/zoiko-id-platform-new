"use client";

import type { ConfigurationIn, CredentialConfigurationItem } from "@zoiko/api-client";
import {
  Badge,
  Button,
  Card,
  Checkbox,
  CopyId,
  DataTable,
  DateTime,
  EmptyState,
  FieldGroup,
  RiskBadge,
  Select,
  StatusBadge,
  TextField,
  humanize,
  type Column,
} from "@zoiko/ui";
import { FileBadge, Plus } from "lucide-react";
import { useState } from "react";

import { listInput, useCellMutation, useCellQuery } from "@/lib/cell";

import { AdminQueryView, FormDialog, R3ActionDialog, joined, required } from "../identity-providers/shared";
import { PROFILES_KEY } from "../verification/profiles";

const NO_PROFILE = "none";

export const CONFIGURATIONS_KEY = ["admin", "credentials", "configurations"];

type Format = ConfigurationIn["format"];
type StatusMethod = ConfigurationIn["status_method"];

/** The backend pairs VC_JWT with Bitstring Status List, SD-JWT VC and mdoc with Token Status List. */
const STATUS_FOR: Record<Format, StatusMethod> = { SD_JWT_VC: "TOKEN_STATUS_LIST", MDOC: "TOKEN_STATUS_LIST", VC_JWT: "BITSTRING_STATUS_LIST" };

function CreateConfigurationDialog({ onClose }: { onClose: () => void }) {
  const profiles = useCellQuery(PROFILES_KEY, (api) => api.verification.profiles());

  const [type, setType] = useState("");
  const [format, setFormat] = useState<Format>("SD_JWT_VC");
  const [requiredClaims, setRequiredClaims] = useState("");
  const [optionalClaims, setOptionalClaims] = useState("");
  const [holderBinding, setHolderBinding] = useState(true);
  const [statusMethod, setStatusMethod] = useState<StatusMethod>("TOKEN_STATUS_LIST");
  const [statusMandatory, setStatusMandatory] = useState(true);
  const [risk, setRisk] = useState("2");
  const [profileId, setProfileId] = useState<string | null>(null);
  const [issuance, setIssuance] = useState(false);
  const [localError, setLocalError] = useState<unknown>(null);

  const create = useCellMutation((api, body: ConfigurationIn) => api.credentials.createConfiguration(body), {
    invalidate: [CONFIGURATIONS_KEY],
    success: "Credential configuration created as a draft",
    onSuccess: onClose,
  });

  function submit() {
    setLocalError(null);
    create.reset();
    try {
      const req = listInput(requiredClaims);
      if (!req.length) throw new Error("Add at least one required claim.");
      if (req.length > 50) throw new Error("At most 50 required claims.");
      const opt = listInput(optionalClaims);
      if (opt.length > 50) throw new Error("At most 50 optional claims.");
      if (statusMethod !== "NONE" && statusMethod !== STATUS_FOR[format])
        throw new Error(`${humanize(format)} uses ${humanize(STATUS_FOR[format])}.`);
      create.mutate({
        credential_type: required(type, "Credential type"),
        format,
        required_claims: req,
        optional_claims: opt,
        holder_binding_required: holderBinding,
        status_method: statusMethod,
        status_mandatory: statusMethod === "NONE" ? false : statusMandatory,
        risk_class: Number(risk),
        assurance_profile_id: profileId && profileId !== NO_PROFILE ? profileId : null,
        issuance_enabled: issuance,
      });
    } catch (e) {
      setLocalError(e);
    }
  }

  return (
    <FormDialog
      isOpen
      onClose={onClose}
      title="New credential configuration"
      description="Defines a credential type this tenant can issue or request. Created as a draft; activating it releases it."
      submitLabel="Create draft"
      pending={create.isPending}
      error={localError ?? create.error}
      onSubmit={submit}
      risk="R3"
    >
      <div className="grid gap-4 sm:grid-cols-2">
        <TextField
          label="Credential type"
          isRequired
          mono
          value={type}
          onChange={setType}
          placeholder="https://credentials.example.com/employee"
          description="vct for SD-JWT VC, doctype for mdoc, type for VCDM."
          className="sm:col-span-2"
        />
        <Select
          label="Format"
          isRequired
          value={format}
          onChange={(v) => {
            setFormat(v as Format);
            if (statusMethod !== "NONE") setStatusMethod(STATUS_FOR[v as Format]);
          }}
          options={[
            { id: "SD_JWT_VC", label: "SD-JWT VC", description: "Selective disclosure JWT (IETF)." },
            { id: "MDOC", label: "mdoc", description: "ISO/IEC 18013-5 mobile document." },
            { id: "VC_JWT", label: "VC-JWT (VCDM 2.0)", description: "W3C Verifiable Credential as JWT." },
          ]}
        />
        <Select
          label="Status method"
          isRequired
          value={statusMethod}
          onChange={(v) => setStatusMethod(v as StatusMethod)}
          options={[
            { id: STATUS_FOR[format], label: humanize(STATUS_FOR[format]), description: "Lets the issuer suspend or revoke." },
            { id: "NONE", label: "None", description: "Credentials cannot be suspended or revoked." },
          ]}
        />
        <TextField label="Required claims" isRequired mono value={requiredClaims} onChange={setRequiredClaims} description="Comma separated, up to 50." />
        <TextField label="Optional claims" mono value={optionalClaims} onChange={setOptionalClaims} description="Selectively disclosable extras." />
        <Select
          label="Risk class"
          value={risk}
          onChange={setRisk}
          options={["0", "1", "2", "3"].map((r) => ({ id: r, label: `R${r}` }))}
          description="Risk of the actions this credential can unlock."
        />
        <Select
          label="Assurance profile (optional)"
          value={profileId}
          onChange={setProfileId}
          options={[{ id: NO_PROFILE, label: "None" }, ...(profiles.data?.items ?? []).map((p) => ({ id: p.profile_id, label: `${p.name} v${p.version}`, description: `${p.level} · ${humanize(p.status)}` }))]}
          placeholder={profiles.isError ? "Profiles unavailable" : "None"}
          description="Identity verification the holder must have to be issued this credential."
        />
      </div>
      <FieldGroup legend="Options">
        <Checkbox isSelected={holderBinding} onChange={setHolderBinding} description="The wallet proves possession of a key bound into the credential.">
          Holder binding required
        </Checkbox>
        <Checkbox
          isSelected={statusMethod !== "NONE" && statusMandatory}
          isDisabled={statusMethod === "NONE"}
          onChange={setStatusMandatory}
          description="Verifiers must check status before accepting."
        >
          Status check mandatory
        </Checkbox>
        <Checkbox
          isSelected={issuance}
          onChange={setIssuance}
          description="Lets wallets obtain it from this tenant. Activating then also needs the credential.issue (R3) authority."
        >
          Issuance enabled
        </Checkbox>
      </FieldGroup>
    </FormDialog>
  );
}

export function ConfigurationsSection() {
  const configs = useCellQuery(CONFIGURATIONS_KEY, (api) => api.credentials.configurations());
  const [creating, setCreating] = useState(false);
  const [target, setTarget] = useState<CredentialConfigurationItem | null>(null);

  const activate = useCellMutation((api, id: string) => api.credentials.activateConfiguration(id), {
    invalidate: [CONFIGURATIONS_KEY],
    success: "Configuration activated",
  });

  const columns: Column<CredentialConfigurationItem>[] = [
    {
      key: "type",
      header: "Credential type",
      pinned: true,
      sortValue: (c) => c.credential_type,
      render: (c) => (
        <div className="min-w-0">
          <div className="max-w-72 truncate font-mono text-xs font-medium text-ink" title={c.credential_type}>
            {c.credential_type}
          </div>
          <CopyId value={c.configuration_id} label="configuration" />
        </div>
      ),
    },
    { key: "format", header: "Format", sortValue: (c) => c.format, render: (c) => <span className="font-mono text-xs">{c.format}</span> },
    { key: "status", header: "Status", sortValue: (c) => c.status, render: (c) => <StatusBadge state={c.status} kind="Configuration" /> },
    { key: "claims", header: "Required claims", render: (c) => joined(c.required_claims) },
    { key: "optional", header: "Optional claims", defaultHidden: true, render: (c) => joined(c.optional_claims) },
    { key: "risk", header: "Risk", sortValue: (c) => c.risk_class, render: (c) => <RiskBadge risk={c.risk_class} compact /> },
    {
      key: "issuance",
      header: "Issuance",
      render: (c) => (c.issuance_enabled ? <Badge tone="info">Enabled</Badge> : <span className="text-xs text-ink-muted">Verify only</span>),
    },
    { key: "statusMethod", header: "Status method", defaultHidden: true, render: (c) => <span className="text-xs">{humanize(c.status_method)}</span> },
    { key: "binding", header: "Holder binding", defaultHidden: true, render: (c) => (c.holder_binding_required ? "Required" : "No") },
    { key: "adapter", header: "Adapter", defaultHidden: true, render: (c) => <span className="font-mono text-xs">{c.adapter_version}</span> },
    { key: "created", header: "Created", defaultHidden: true, sortValue: (c) => c.created_at, render: (c) => <DateTime value={c.created_at} /> },
    {
      key: "actions",
      header: "Actions",
      align: "end",
      render: (c) =>
        c.status === "DRAFT" ? (
          <Button size="xs" variant="secondary" onPress={() => setTarget(c)}>
            Activate
          </Button>
        ) : null,
    },
  ];

  return (
    <>
      <Card
        padded={false}
        title="Credential configurations"
        description="Credential types this tenant issues or requests from wallets."
        actions={
          <Button size="sm" icon={<Plus aria-hidden className="size-4" />} onPress={() => setCreating(true)}>
            New configuration
          </Button>
        }
      >
        <AdminQueryView query={configs}>
          {(data) => (
            <DataTable
              caption="Credential configurations"
              columns={columns}
              rows={data.items}
              rowKey={(c) => c.configuration_id}
              empty={
                <EmptyState icon={FileBadge} title="No credential configurations">
                  Create a configuration to issue credentials or to request them in presentations.
                </EmptyState>
              }
            />
          )}
        </AdminQueryView>
      </Card>

      {creating ? <CreateConfigurationDialog onClose={() => setCreating(false)} /> : null}
      {target ? (
        <R3ActionDialog
          isOpen
          onClose={() => setTarget(null)}
          title="Activate credential configuration"
          intent={
            <p>
              The configuration becomes usable for presentations
              {target.issuance_enabled ? " and wallets can start obtaining this credential. Because issuance is enabled, this also needs your credential.issue authority" : ""}
              .
            </p>
          }
          scope={[
            ["Type", <span key="t" className="break-all font-mono text-xs">{target.credential_type}</span>],
            ["Format", target.format],
            ["Issuance", target.issuance_enabled ? "Enabled" : "Verify only"],
          ]}
          reversibility="irreversible"
          commitLabel="Activate configuration"
          run={async () => {
            const r = await activate.mutateAsync(target.configuration_id);
            return { outcome: r.status, objectId: r.configuration_id };
          }}
        />
      ) : null}
    </>
  );
}
