"use client";

import type { BuildItem } from "@zoiko/api-client";
import { useQueryClient } from "@tanstack/react-query";
import { Banner, Button, Card, CopyId, DataTable, DateTime, Dialog, EmptyState, StatusBadge, TextField, useToast, type Column } from "@zoiko/ui";
import { PackageCheck, Plus } from "lucide-react";
import { useState } from "react";

import { ApiErrorView, QueryView, useCellMutation, useCellQuery } from "@/lib/cell";
import { useRuntime } from "@/lib/runtime";

import { PersonLabel, usePeople } from "../team/person-label";
import { NEEDS } from "./agents-logic";
import { AuthorityNotice, DigestHelp } from "./pickers";
import { AGENTS_KEY, BLUEPRINTS_KEY, BUILDS_KEY, DIGEST_RE } from "./shared";

/**
 * The reviewed software a blueprint release may run. Whoever builds the software registers its digest here once; a second person
 * approves it; everyone else then simply picks it when drafting a release. This is the one place a digest is entered.
 */
export function BuildsSection() {
  const builds = useCellQuery(BUILDS_KEY, (api) => api.agents.builds());
  const person = usePeople();
  const [creating, setCreating] = useState(false);
  const [revoking, setRevoking] = useState<BuildItem | null>(null);
  const [reason, setReason] = useState("SUPPLY_CHAIN_COMPROMISE");

  const approve = useCellMutation((api, b: BuildItem) => api.agents.approveBuild(b.build_id), {
    invalidate: [BUILDS_KEY],
    success: "Build approved. Releases can now be drafted from it.",
  });
  const revoke = useCellMutation((api, b: BuildItem) => api.agents.revokeBuild(b.build_id, reason), {
    invalidate: [BUILDS_KEY, BLUEPRINTS_KEY, AGENTS_KEY],
    success: (r) => `Revoked: ${r.releases_revoked} release(s) revoked, ${r.bindings_suspended} binding(s) and ${r.agents_suspended} agent(s) suspended`,
    onSuccess: () => setRevoking(null),
  });

  const columns: Column<BuildItem>[] = [
    { key: "name", header: "Build", pinned: true, render: (b) => <span className="text-sm font-medium">{b.name}</span>, sortValue: (b) => b.name },
    { key: "review", header: "Review", render: (b) => <StatusBadge state={b.review_status} kind="Blueprint review" />, sortValue: (b) => b.review_status },
    { key: "digest", header: "Digest", render: (b) => <CopyId value={b.artifact_digest} label="build digest" /> },
    { key: "source", header: "From", render: (b) => (b.source_ref ? <span className="font-mono text-xs">{b.source_ref}</span> : "—"), defaultHidden: true },
    { key: "registered", header: "Registered by", render: (b) => <PersonLabel person={person(b.registered_by)} fallbackId={b.registered_by} /> },
    { key: "approved", header: "Approved by", render: (b) => (b.approved_by ? <PersonLabel person={person(b.approved_by)} fallbackId={b.approved_by} /> : "—") },
    { key: "when", header: "Registered", render: (b) => <DateTime value={b.registered_at} />, sortValue: (b) => b.registered_at },
    {
      key: "actions",
      header: "Actions",
      align: "end",
      render: (b) => (
        <span className="inline-flex gap-2">
          {b.review_status === "DRAFT" ? (
            <Button size="xs" pending={approve.isPending && approve.variables?.build_id === b.build_id} onPress={() => approve.mutate(b)}>
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

  const closeRevoke = () => {
    if (revoke.isPending) return;
    setRevoking(null);
    revoke.reset();
  };

  return (
    <>
      <Card
        padded={false}
        title="Approved builds"
        description="The software an agent may run. Register a build once; a second person approves it; then anyone drafting a blueprint release picks it from a list."
        actions={
          <Button size="sm" icon={<Plus aria-hidden className="size-4" />} onPress={() => setCreating(true)}>
            Register build
          </Button>
        }
      >
        <QueryView query={builds}>
          {(data) => (
            <>
              <div className="space-y-3 p-4 pb-0">
                <AuthorityNotice needs={NEEDS.build} what="register or approve a build" />
                <Banner tone={data.enforced ? "info" : "warn"} title={data.enforced ? "Releases must run an approved build" : "Releases may run any digest in this environment"}>
                  {data.enforced
                    ? "A blueprint release is refused unless its build is approved here."
                    : "Approved builds are not required here (a local, test or development setup). A deployed environment requires them."}
                </Banner>
                {approve.error ? <ApiErrorView error={approve.error} /> : null}
              </div>
              <DataTable
                caption="Approved builds"
                columns={columns}
                rows={data.items}
                rowKey={(b) => b.build_id}
                empty={
                  <EmptyState icon={PackageCheck} title="No builds registered">
                    Register the build of the software an agent will run. Someone else approves it, and then it can back a blueprint release.
                  </EmptyState>
                }
              />
            </>
          )}
        </QueryView>
      </Card>

      <RegisterBuildDialog isOpen={creating} onClose={() => setCreating(false)} />

      <Dialog
        isOpen={revoking !== null}
        onOpenChange={(o) => !o && closeRevoke()}
        title="Revoke this build?"
        size="sm"
        role="alertdialog"
        footer={
          <>
            <Button variant="secondary" isDisabled={revoke.isPending} onPress={closeRevoke}>Cancel</Button>
            <Button variant="danger" pending={revoke.isPending} onPress={() => revoking && revoke.mutate(revoking)}>
              Revoke build
            </Button>
          </>
        }
      >
        <div className="space-y-3 text-sm text-ink-muted">
          <p className="font-medium text-ink">{revoking?.name}</p>
          <p>
            Every release drafted from this build is revoked, every workload binding running it is suspended and every active agent built on it is
            suspended. Their history is kept as evidence. This cannot be undone: register the next build instead.
          </p>
          <TextField label="Reason code" mono value={reason} onChange={(v) => setReason(v.toUpperCase())} description="Capital letters, digits and underscores." />
          {revoke.error ? <ApiErrorView error={revoke.error} /> : null}
        </div>
      </Dialog>
    </>
  );
}

function RegisterBuildDialog({ isOpen, onClose }: { isOpen: boolean; onClose: () => void }) {
  const { api, protect } = useRuntime();
  const qc = useQueryClient();
  const toast = useToast();
  const [name, setName] = useState("");
  const [digest, setDigest] = useState("");
  const [source, setSource] = useState("");
  const [provenance, setProvenance] = useState("");
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
    if (name.trim().length < 2) return setFormError("Give the build a name people will recognise.");
    if (!DIGEST_RE.test(digest.trim())) return setFormError("The digest must be sha256: followed by 64 hex characters.");
    setPending(true);
    try {
      await protect(() =>
        api.agents.registerBuild({
          name: name.trim(),
          artifact_digest: digest.trim(),
          source_ref: source.trim() || null,
          provenance_ref: provenance.trim() || null,
        }),
      );
      await qc.invalidateQueries({ queryKey: BUILDS_KEY });
      toast.notify({ tone: "ok", title: "Build registered", body: "Someone other than you must approve it before a release can use it." });
      setName("");
      setDigest("");
      setSource("");
      setProvenance("");
      onClose();
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
      title="Register build"
      description="Record the software an agent will run. A second person approves it before it can be used."
      size="lg"
      footer={
        <>
          <Button variant="secondary" isDisabled={pending} onPress={close}>Cancel</Button>
          <Button pending={pending} onPress={() => void submit()}>Register build</Button>
        </>
      }
    >
      <div className="space-y-4">
        <AuthorityNotice needs={NEEDS.build} what="register a build" />
        <TextField label="Name" isRequired value={name} onChange={setName} placeholder="Invoice bot, release 1.4" description="What people will pick from a list later." />
        <TextField
          label="Build digest"
          isRequired
          mono
          value={digest}
          onChange={setDigest}
          placeholder="sha256:…"
          description="From whoever builds it: the image digest shown by your registry or build pipeline. This is the only place a digest is typed."
          errorMessage={digest.trim() && !DIGEST_RE.test(digest.trim()) ? "sha256: followed by 64 hex characters." : undefined}
        />
        <DigestHelp canUseTestBuild={false} />
        <TextField label="Where it comes from (optional)" mono value={source} onChange={setSource} placeholder="ghcr.io/acme/invoice-bot" description="The registry reference or repository." />
        <TextField label="Proof of how it was built (optional)" mono value={provenance} onChange={setProvenance} placeholder="https://…" description="A link to the build's provenance or signature. It is recorded, not verified." />
        {formError ? <Banner tone="danger" title="Check the form" live="polite">{formError}</Banner> : null}
        {error ? <ApiErrorView error={error} /> : null}
      </div>
    </Dialog>
  );
}
