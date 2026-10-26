"use client";

import type { BlueprintRelease } from "@zoiko/api-client";
import { useQueryClient } from "@tanstack/react-query";
import { Banner, Button, Card, CopyId, DataTable, DateTime, Dialog, EmptyState, StatusBadge, TextField, useToast, type Column } from "@zoiko/ui";
import { PackageCheck, Plus } from "lucide-react";
import { useState } from "react";

import { ApiErrorView, QueryView, useCellMutation, useCellQuery } from "@/lib/cell";
import { useRuntime } from "@/lib/runtime";

import { AGENTS_KEY, BLUEPRINTS_KEY, DIGEST_RE, UUID_RE } from "./shared";

export function BlueprintsSection() {
  const blueprints = useCellQuery(BLUEPRINTS_KEY, (api) => api.agents.blueprints());
  const [creating, setCreating] = useState(false);
  const [revoking, setRevoking] = useState<BlueprintRelease | null>(null);
  const [reason, setReason] = useState("SUPPLY_CHAIN_COMPROMISE");

  const approve = useCellMutation((api, b: BlueprintRelease) => api.agents.approveBlueprint(b.blueprint_id, b.version), {
    invalidate: [BLUEPRINTS_KEY],
    success: "Release approved. It is now immutable.",
  });
  const revoke = useCellMutation((api, b: BlueprintRelease) => api.agents.revokeBlueprint(b.blueprint_id, b.version, reason), {
    invalidate: [BLUEPRINTS_KEY, AGENTS_KEY],
    success: (r) => `Revoked: ${r.bindings_suspended} binding(s) and ${r.agents_suspended} agent(s) suspended`,
    onSuccess: () => setRevoking(null),
  });

  const columns: Column<BlueprintRelease>[] = [
    {
      key: "bp",
      header: "Release",
      pinned: true,
      render: (b) => <span className="font-mono text-xs">{b.blueprint_id} @ {b.version}</span>,
      sortValue: (b) => `${b.blueprint_id}@${b.version}`,
    },
    { key: "review", header: "Review", render: (b) => <StatusBadge state={b.review_status} kind="Blueprint review" />, sortValue: (b) => b.review_status },
    { key: "artifact", header: "Artifact digest", render: (b) => <CopyId value={b.artifact_digest} label="artifact digest" /> },
    { key: "tools", header: "Tool manifest", render: (b) => <CopyId value={b.tool_manifest_digest} label="tool manifest digest" />, defaultHidden: true },
    { key: "publisher", header: "Publisher", render: (b) => <CopyId value={b.publisher_principal_id} label="publisher" />, defaultHidden: true },
    { key: "when", header: "Effective", render: (b) => (b.effective_at ? <DateTime value={b.effective_at} /> : "—") },
    {
      key: "actions",
      header: "Actions",
      align: "end",
      render: (b) => (
        <span className="inline-flex gap-2">
          {b.review_status === "DRAFT" ? (
            <Button size="xs" pending={approve.isPending} onPress={() => approve.mutate(b)}>
              Approve
            </Button>
          ) : null}
          {b.review_status !== "REVOKED" ? (
            <Button size="xs" variant="danger-outline" onPress={() => setRevoking(b)}>
              Revoke
            </Button>
          ) : null}
        </span>
      ),
    },
  ];

  const close = () => {
    if (revoke.isPending) return;
    setRevoking(null);
    revoke.reset();
  };

  return (
    <>
      <Card
        padded={false}
        title="Blueprint releases"
        description="The immutable design an agent is built from. A release is approved by someone other than its publisher; revoking one stops everything running its artifact."
        actions={
          <Button size="sm" icon={<Plus aria-hidden className="size-4" />} onPress={() => setCreating(true)}>
            Draft release
          </Button>
        }
      >
        {approve.error ? <ApiErrorView error={approve.error} /> : null}
        <QueryView query={blueprints}>
          {(data) => (
            <DataTable
              caption="Blueprint releases"
              columns={columns}
              rows={data.items}
              rowKey={(b) => `${b.blueprint_id}@${b.version}`}
              empty={
                <EmptyState icon={PackageCheck} title="No blueprint releases">
                  Draft a release with the signed artifact digest, then have someone else approve it.
                </EmptyState>
              }
            />
          )}
        </QueryView>
      </Card>

      <DraftBlueprintDialog isOpen={creating} onClose={() => setCreating(false)} />

      <Dialog
        isOpen={revoking !== null}
        onOpenChange={(o) => !o && close()}
        title="Revoke this release?"
        size="sm"
        role="alertdialog"
        footer={
          <>
            <Button variant="secondary" isDisabled={revoke.isPending} onPress={close}>Cancel</Button>
            <Button variant="danger" pending={revoke.isPending} onPress={() => revoking && revoke.mutate(revoking)}>
              Revoke release
            </Button>
          </>
        }
      >
        <div className="space-y-3 text-sm text-ink-muted">
          <p className="font-mono text-xs">{revoking?.blueprint_id} @ {revoking?.version}</p>
          <p>
            Every live runtime binding running this artifact is suspended and every active agent built on it is suspended. Their history is kept as evidence.
          </p>
          <TextField label="Reason code" mono value={reason} onChange={setReason} />
          {revoke.error ? <ApiErrorView error={revoke.error} /> : null}
        </div>
      </Dialog>
    </>
  );
}

function DraftBlueprintDialog({ isOpen, onClose }: { isOpen: boolean; onClose: () => void }) {
  const { api, protect } = useRuntime();
  const qc = useQueryClient();
  const toast = useToast();
  const [id, setId] = useState("");
  const [version, setVersion] = useState("");
  const [artifact, setArtifact] = useState("");
  const [manifest, setManifest] = useState("");
  const [publisher, setPublisher] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [formError, setFormError] = useState<string | null>(null);

  function close() {
    if (pending) return;
    setError(null);
    setFormError(null);
    onClose();
  }

  async function submit() {
    setFormError(null);
    setError(null);
    if (!/^[a-z0-9][a-z0-9._-]{1,62}$/.test(id.trim())) return setFormError("Blueprint ID: lowercase letters, digits, dot, dash or underscore (2 to 63 characters).");
    if (!version.trim()) return setFormError("Give the release a version.");
    if (!DIGEST_RE.test(artifact.trim())) return setFormError("The artifact digest must be sha256: followed by 64 hex characters.");
    if (!DIGEST_RE.test(manifest.trim())) return setFormError("The tool manifest digest must be sha256: followed by 64 hex characters.");
    if (!UUID_RE.test(publisher.trim())) return setFormError("The publisher must be a principal ID.");
    setPending(true);
    try {
      await protect(() =>
        api.agents.createBlueprint({
          blueprint_id: id.trim(),
          version: version.trim(),
          artifact_digest: artifact.trim(),
          tool_manifest_digest: manifest.trim(),
          publisher_principal_id: publisher.trim(),
        }),
      );
      await qc.invalidateQueries({ queryKey: BLUEPRINTS_KEY });
      toast.notify({ tone: "ok", title: "Release drafted", body: "Someone other than the publisher must approve it." });
      close();
    } catch (e) {
      setError(e);
    } finally {
      setPending(false);
    }
  }

  return (
    <Dialog
      isOpen={isOpen}
      onOpenChange={(o) => !o && close()}
      title="Draft blueprint release"
      description="Releases are immutable: to change anything, publish a new version."
      size="lg"
      footer={
        <>
          <Button variant="secondary" isDisabled={pending} onPress={close}>Cancel</Button>
          <Button pending={pending} onPress={() => void submit()}>Draft release</Button>
        </>
      }
    >
      <div className="space-y-4">
        <div className="grid gap-4 sm:grid-cols-2">
          <TextField label="Blueprint ID" isRequired mono value={id} onChange={setId} placeholder="finance-approval-bot" />
          <TextField label="Version" isRequired mono value={version} onChange={setVersion} placeholder="1.0.0" />
        </div>
        <TextField label="Artifact digest" isRequired mono value={artifact} onChange={setArtifact} placeholder="sha256:…" description="The signed build or container digest that will run." />
        <TextField label="Tool manifest digest" isRequired mono value={manifest} onChange={setManifest} placeholder="sha256:…" />
        <TextField label="Publisher principal ID" isRequired mono value={publisher} onChange={setPublisher} description="Who built and published this release; they cannot approve it." />
        {formError ? <Banner tone="danger" title="Check the form" live="polite">{formError}</Banner> : null}
        {error ? <ApiErrorView error={error} /> : null}
      </div>
    </Dialog>
  );
}
