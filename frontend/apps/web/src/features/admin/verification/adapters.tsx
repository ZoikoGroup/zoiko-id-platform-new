"use client";

import type { AdapterIn, VerificationAdapter, VerificationAdapterItem } from "@zoiko/api-client";
import { Badge, Banner, Button, Card, CopyId, DataTable, DateTime, Dialog, EmptyState, StatusBadge, TextField, type Column } from "@zoiko/ui";
import { Plug, Plus } from "lucide-react";
import { useState } from "react";

import { jsonInput, listInput, useCellMutation, useCellQuery } from "@/lib/cell";

import { AdminQueryView, AlgorithmPicker, FormDialog, R3ActionDialog, joined, required, type Alg } from "../identity-providers/shared";

export const ADAPTERS_KEY = ["admin", "verification", "adapters"];

/** Circuit-breaker state from consecutive failures and open_until. */
function Circuit({ a }: { a: VerificationAdapterItem }) {
  if (a.open_until) {
    return (
      <div className="space-y-0.5">
        <Badge tone="danger" label={`Circuit open until ${a.open_until}`}>
          Open
        </Badge>
        <div className="text-2xs text-ink-subtle">
          until <DateTime value={a.open_until} />
        </div>
      </div>
    );
  }
  return a.consecutive_failures > 0 ? (
    <Badge tone="warn" label={`Circuit closed, ${a.consecutive_failures} consecutive failures`}>
      Closed · {a.consecutive_failures} failing
    </Badge>
  ) : (
    <Badge tone="ok">Closed</Badge>
  );
}

function RegisterAdapterDialog({ onClose }: { onClose: () => void }) {
  const [adapterId, setAdapterId] = useState("");
  const [provider, setProvider] = useState("");
  const [mappingVersion, setMappingVersion] = useState("1");
  const [endpoint, setEndpoint] = useState("");
  const [jwks, setJwks] = useState("");
  const [algorithms, setAlgorithms] = useState<Alg[]>(["ES256"]);
  const [group, setGroup] = useState("");
  const [evidence, setEvidence] = useState("GOVERNMENT_ID");
  const [jurisdictions, setJurisdictions] = useState("");
  const [localError, setLocalError] = useState<unknown>(null);
  const [created, setCreated] = useState<VerificationAdapter | null>(null);

  const create = useCellMutation((api, body: AdapterIn) => api.verification.createAdapter(body), {
    invalidate: [ADAPTERS_KEY],
    success: (r) => `Adapter ${r.adapter_id} registered`,
    onSuccess: (r) => setCreated(r),
  });

  function submit() {
    setLocalError(null);
    create.reset();
    try {
      const id = required(adapterId, "Adapter ID");
      if (!/^[a-z0-9][a-z0-9_-]{1,62}$/.test(id)) throw new Error("Adapter ID: 2–63 lowercase letters, digits, - or _, starting with a letter or digit.");
      const url = required(endpoint, "Submit endpoint");
      if (!url.startsWith("https://")) throw new Error("Submit endpoint must be an https:// URL.");
      const keys = jsonInput(jwks, "Callback JWKS");
      if (!keys) throw new Error("Callback JWKS is required.");
      if (!algorithms.length) throw new Error("Choose at least one callback algorithm.");
      const ev = listInput(evidence);
      if (!ev.length) throw new Error("Add at least one supported evidence type.");
      const juris = listInput(jurisdictions).map((j) => j.toUpperCase());
      if (!juris.length) throw new Error("Add at least one jurisdiction.");
      create.mutate({
        adapter_id: id,
        provider: required(provider, "Provider"),
        mapping_version: required(mappingVersion, "Mapping version"),
        submit_endpoint: url,
        callback_jwks: keys,
        callback_algorithms: algorithms,
        equivalence_group: required(group, "Equivalence group"),
        supported_evidence: ev,
        jurisdictions: juris,
      });
    } catch (e) {
      setLocalError(e);
    }
  }

  if (created) {
    return (
      <Dialog isOpen onOpenChange={(o) => !o && onClose()} title="Adapter registered" size="md" footer={<Button onPress={onClose}>Done</Button>}>
        <div className="space-y-3 text-sm text-ink-muted">
          <p>
            Configure the provider to send signed results to this callback path on the cell&apos;s public origin. Callbacks are only accepted
            when signed with a key from the pinned JWKS.
          </p>
          {created.callback_path ? (
            <div className="rounded-lg border border-border bg-canvas/60 p-3">
              <CopyId value={created.callback_path} short={false} label="callback path" />
            </div>
          ) : null}
        </div>
      </Dialog>
    );
  }

  return (
    <FormDialog
      isOpen
      onClose={onClose}
      title="Register verification adapter"
      description="Connect an external identity verification provider. Its callback signing keys are pinned here."
      submitLabel="Register adapter"
      pending={create.isPending}
      error={localError ?? create.error}
      onSubmit={submit}
      risk="R3"
    >
      <div className="grid gap-4 sm:grid-cols-2">
        <TextField label="Adapter ID" isRequired mono value={adapterId} onChange={setAdapterId} placeholder="acme-idv" description="Lowercase, permanent." />
        <TextField label="Provider" isRequired value={provider} onChange={setProvider} placeholder="Acme IDV" />
        <TextField label="Mapping version" isRequired mono value={mappingVersion} onChange={setMappingVersion} description="Version of the result mapping." />
        <TextField
          label="Equivalence group"
          isRequired
          mono
          value={group}
          onChange={setGroup}
          description="Adapters in one group can stand in for each other when a circuit opens."
        />
        <TextField
          label="Submit endpoint"
          isRequired
          mono
          type="url"
          value={endpoint}
          onChange={setEndpoint}
          placeholder="https://api.provider.example/verifications"
          className="sm:col-span-2"
        />
        <TextField
          label="Supported evidence"
          isRequired
          mono
          value={evidence}
          onChange={setEvidence}
          description="Comma separated, e.g. GOVERNMENT_ID, BIOMETRIC_BINDING."
        />
        <TextField label="Jurisdictions" isRequired mono value={jurisdictions} onChange={setJurisdictions} placeholder="GB, IE" />
      </div>
      <TextField
        label="Callback JWKS"
        isRequired
        multiline
        rows={5}
        mono
        value={jwks}
        onChange={setJwks}
        placeholder='{"keys": [ … ]}'
        description="Keys the provider signs callbacks with. Never shown again."
      />
      <AlgorithmPicker legend="Callback signing algorithms" value={algorithms} onChange={setAlgorithms} />
    </FormDialog>
  );
}

export function AdaptersSection() {
  const adapters = useCellQuery(ADAPTERS_KEY, (api) => api.verification.adapters());
  const [creating, setCreating] = useState(false);
  const [target, setTarget] = useState<VerificationAdapterItem | null>(null);

  const quarantine = useCellMutation((api, id: string) => api.verification.quarantineAdapter(id), {
    invalidate: [ADAPTERS_KEY],
    success: (r) => `Adapter ${r.adapter_id} quarantined`,
  });

  const columns: Column<VerificationAdapterItem>[] = [
    {
      key: "adapter",
      header: "Adapter",
      pinned: true,
      sortValue: (a) => a.adapter_id,
      render: (a) => (
        <div className="min-w-0">
          <div className="font-mono text-xs font-medium text-ink">{a.adapter_id}</div>
          <div className="text-xs text-ink-muted">{a.provider}</div>
        </div>
      ),
    },
    { key: "status", header: "Status", sortValue: (a) => a.status, render: (a) => <StatusBadge state={a.status} kind="Adapter" /> },
    { key: "circuit", header: "Circuit breaker", sortValue: (a) => a.consecutive_failures, render: (a) => <Circuit a={a} /> },
    {
      key: "failures",
      header: "Consecutive failures",
      defaultHidden: true,
      sortValue: (a) => a.consecutive_failures,
      render: (a) => <span className="tabular">{a.consecutive_failures}</span>,
    },
    { key: "group", header: "Equivalence group", render: (a) => <span className="font-mono text-xs">{a.equivalence_group}</span> },
    { key: "evidence", header: "Evidence", render: (a) => joined(a.supported_evidence) },
    { key: "jurisdictions", header: "Jurisdictions", render: (a) => joined(a.jurisdictions) },
    { key: "mapping", header: "Mapping", defaultHidden: true, render: (a) => <span className="font-mono text-xs">{a.mapping_version}</span> },
    {
      key: "endpoint",
      header: "Submit endpoint",
      defaultHidden: true,
      render: (a) => (
        <span className="block max-w-64 truncate font-mono text-xs" title={a.submit_endpoint}>
          {a.submit_endpoint}
        </span>
      ),
    },
    { key: "algs", header: "Callback algorithms", defaultHidden: true, render: (a) => joined(a.callback_algorithms) },
    {
      key: "actions",
      header: "Actions",
      align: "end",
      render: (a) =>
        a.status !== "QUARANTINED" ? (
          <Button size="xs" variant="danger-outline" onPress={() => setTarget(a)}>
            Quarantine
          </Button>
        ) : null,
    },
  ];

  return (
    <>
      <Card
        padded={false}
        title="Verification adapters"
        description="External verification providers. A circuit opens after repeated failures and closes again on its own."
        actions={
          <Button size="sm" icon={<Plus aria-hidden className="size-4" />} onPress={() => setCreating(true)}>
            Register adapter
          </Button>
        }
      >
        <AdminQueryView query={adapters}>
          {(data) => (
            <DataTable
              caption="Verification adapters"
              columns={columns}
              rows={data.items}
              rowKey={(a) => a.adapter_id}
              empty={
                <EmptyState icon={Plug} title="No verification adapters">
                  Register a provider adapter so verifications can be sent somewhere.
                </EmptyState>
              }
            />
          )}
        </AdminQueryView>
      </Card>

      {creating ? <RegisterAdapterDialog onClose={() => setCreating(false)} /> : null}
      {target ? (
        <R3ActionDialog
          isOpen
          onClose={() => setTarget(null)}
          title={`Quarantine ${target.adapter_id}`}
          intent={
            <div className="space-y-2">
              <p>
                The adapter stops receiving new verifications and its callbacks are refused from now on. Use this when a provider is compromised
                or misbehaving.
              </p>
              <Banner tone="warn">There is no un-quarantine action in the console; a replacement adapter needs a new ID.</Banner>
            </div>
          }
          scope={[
            ["Adapter", <span key="a" className="font-mono">{target.adapter_id}</span>],
            ["Provider", target.provider],
            ["Equivalence group", target.equivalence_group],
          ]}
          reversibility="irreversible"
          dependents={[{ label: "Verifications in progress with this provider", effect: "Results refused" }]}
          commitLabel="Quarantine adapter"
          run={async () => {
            const r = await quarantine.mutateAsync(target.adapter_id);
            return { outcome: r.status, objectId: r.adapter_id };
          }}
        />
      ) : null}
    </>
  );
}
