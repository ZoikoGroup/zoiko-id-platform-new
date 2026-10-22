"use client";

import type { AnchorIn, TrustAnchorItem } from "@zoiko/api-client";
import { Button, Card, CopyId, DataTable, DateTime, EmptyState, RevisionChip, StatusBadge, TextField, type Column } from "@zoiko/ui";
import { Anchor, Plus } from "lucide-react";
import { useState } from "react";

import { jsonInput, listInput, useCellMutation, useCellQuery } from "@/lib/cell";

import { ANCHORS_KEY } from "./integration-form";
import { AdminQueryView, FormDialog, R3ActionDialog, joined, optional, required } from "./shared";

function CreateAnchorDialog({ onClose }: { onClose: () => void }) {
  const [entityId, setEntityId] = useState("");
  const [jwks, setJwks] = useState("");
  const [domains, setDomains] = useState("");
  const [approvalId, setApprovalId] = useState("");
  const [localError, setLocalError] = useState<unknown>(null);

  const create = useCellMutation((api, body: AnchorIn) => api.federation.createTrustAnchor(body), {
    invalidate: [ANCHORS_KEY],
    success: (r) => `Trust anchor ${r.entity_id} added`,
    onSuccess: onClose,
  });

  function submit() {
    setLocalError(null);
    create.reset();
    try {
      const entity = required(entityId, "Entity ID");
      if (!entity.startsWith("https://")) throw new Error("Entity ID must be an https:// URL.");
      const keys = jsonInput(jwks, "JWKS");
      if (!keys || !Array.isArray(keys.keys) || !keys.keys.length) throw new Error('JWKS must pin at least one key in a "keys" array.');
      const allowed = listInput(domains);
      if (!allowed.length) throw new Error("Add at least one allowed domain.");
      if (allowed.length > 20) throw new Error("At most 20 allowed domains.");
      create.mutate({ entity_id: entity, jwks: keys, allowed_domains: allowed, approval_request_id: optional(approvalId) });
    } catch (e) {
      setLocalError(e);
    }
  }

  return (
    <FormDialog
      isOpen
      onClose={onClose}
      title="Add trust anchor"
      description="An OpenID Federation trust anchor whose keys are pinned here. Providers can only be resolved through an anchor added on this page."
      submitLabel="Add trust anchor"
      pending={create.isPending}
      error={localError ?? create.error}
      onSubmit={submit}
      risk="R3"
    >
      <TextField label="Entity ID" isRequired mono type="url" value={entityId} onChange={setEntityId} placeholder="https://anchor.example.org" />
      <TextField
        label="Pinned JWKS"
        isRequired
        multiline
        rows={6}
        mono
        value={jwks}
        onChange={setJwks}
        placeholder='{"keys": [ … ]}'
        description="The anchor's federation signing keys. They are stored and counted, never shown again."
      />
      <TextField
        label="Allowed domains"
        isRequired
        multiline
        rows={3}
        mono
        value={domains}
        onChange={setDomains}
        placeholder="example.org"
        description="Comma or newline separated, up to 20. Only entities under these domains are accepted through this anchor."
      />
      <TextField
        label="Approval request ID (optional)"
        mono
        value={approvalId}
        onChange={setApprovalId}
        description="Only if your tenant policy requires a second administrator's approval for this change."
      />
    </FormDialog>
  );
}

export function TrustAnchorsSection() {
  const anchors = useCellQuery(ANCHORS_KEY, (api) => api.federation.trustAnchors());
  const [creating, setCreating] = useState(false);
  const [target, setTarget] = useState<TrustAnchorItem | null>(null);

  const disable = useCellMutation((api, id: string) => api.federation.disableTrustAnchor(id), {
    invalidate: [ANCHORS_KEY],
    success: "Trust anchor quarantined",
  });

  const columns: Column<TrustAnchorItem>[] = [
    {
      key: "entity",
      header: "Entity ID",
      pinned: true,
      sortValue: (a) => a.entity_id,
      render: (a) => (
        <div className="min-w-0">
          <div className="max-w-72 truncate font-mono text-xs text-ink" title={a.entity_id}>
            {a.entity_id}
          </div>
          <CopyId value={a.anchor_id} label="trust anchor" />
        </div>
      ),
    },
    { key: "domains", header: "Allowed domains", render: (a) => joined(a.allowed_domains) },
    { key: "keys", header: "Pinned keys", sortValue: (a) => a.pinned_keys, render: (a) => <span className="tabular">{a.pinned_keys}</span> },
    { key: "status", header: "Status", sortValue: (a) => a.status, render: (a) => <StatusBadge state={a.status} kind="Trust anchor" /> },
    { key: "revision", header: "Revision", defaultHidden: true, render: (a) => <RevisionChip kind="rev" value={a.revision} /> },
    { key: "created", header: "Added", sortValue: (a) => a.created_at, render: (a) => <DateTime value={a.created_at} /> },
    {
      key: "actions",
      header: "Actions",
      align: "end",
      render: (a) =>
        a.status === "ACTIVE" ? (
          <Button size="xs" variant="danger-outline" onPress={() => setTarget(a)}>
            Disable
          </Button>
        ) : null,
    },
  ];

  return (
    <>
      <Card
        padded={false}
        title="OpenID Federation trust anchors"
        description="Pinned roots of trust for resolving OpenID providers through a federation. Every change here is R3."
        actions={
          <Button size="sm" icon={<Plus aria-hidden className="size-4" />} onPress={() => setCreating(true)}>
            Add trust anchor
          </Button>
        }
      >
        <AdminQueryView query={anchors}>
          {(data) => (
            <DataTable
              caption="Trust anchors"
              columns={columns}
              rows={data.items}
              rowKey={(a) => a.anchor_id}
              empty={
                <EmptyState icon={Anchor} title="No trust anchors">
                  Only needed for providers that are resolved through OpenID Federation instead of pinned configuration.
                </EmptyState>
              }
            />
          )}
        </AdminQueryView>
      </Card>

      {creating ? <CreateAnchorDialog onClose={() => setCreating(false)} /> : null}
      {target ? (
        <R3ActionDialog
          isOpen
          onClose={() => setTarget(null)}
          title="Disable trust anchor"
          intent={
            <p>
              The anchor is quarantined at once. Providers resolved through it stop validating, so sign-in through those integrations fails until
              they are moved to another anchor or to pinned configuration.
            </p>
          }
          scope={[
            ["Entity ID", <span key="e" className="break-all font-mono text-xs">{target.entity_id}</span>],
            ["Anchor", <CopyId key="a" value={target.anchor_id} label="trust anchor" />],
          ]}
          reversibility="irreversible"
          dependents={[{ label: "Integrations resolved through this anchor", effect: "Sign-in fails" }]}
          commitLabel="Disable trust anchor"
          run={async () => {
            const r = await disable.mutateAsync(target.anchor_id);
            return { outcome: r.status, objectId: r.anchor_id };
          }}
        />
      ) : null}
    </>
  );
}
