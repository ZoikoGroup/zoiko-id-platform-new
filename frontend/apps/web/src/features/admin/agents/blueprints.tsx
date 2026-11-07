"use client";

import type { BlueprintRelease } from "@zoiko/api-client";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Banner, Button, Card, CopyId, DataTable, DateTime, Dialog, EmptyState, Select, StatusBadge, TextField, useToast, type Column } from "@zoiko/ui";
import { PackageCheck, Plus } from "lucide-react";
import { useState } from "react";

import { ApiErrorView, QueryView, useCellMutation, useCellQuery } from "@/lib/cell";
import { useRuntime } from "@/lib/runtime";

import { useMe } from "../authority/shared";
import { BUILD_PASTE, BUILD_TEST, NEEDS, allowTestBuilds, buildChoices, digestFor, manifestDigest, nextVersion, testBuildDigest } from "./agents-logic";
import { AuthorityNotice, PersonSelect, ToolChecklist } from "./pickers";
import { AGENTS_KEY, BLUEPRINTS_KEY, BUILDS_KEY, DIGEST_RE, TOOLS_KEY, UUID_RE } from "./shared";

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

const NEW_BLUEPRINT = "__new__";

function DraftBlueprintDialog({ isOpen, onClose }: { isOpen: boolean; onClose: () => void }) {
  const { api, protect } = useRuntime();
  const qc = useQueryClient();
  const toast = useToast();
  const me = useMe();
  const existing = useCellQuery(BLUEPRINTS_KEY, (a) => a.agents.blueprints(), { enabled: isOpen });
  const tools = useCellQuery(TOOLS_KEY, (a) => a.agents.tools(), { enabled: isOpen });
  const builds = useCellQuery(BUILDS_KEY, (a) => a.agents.builds(), { enabled: isOpen });
  const releases = existing.data?.items ?? [];
  const known = [...new Set(releases.map((b) => b.blueprint_id))];

  const [which, setWhich] = useState(NEW_BLUEPRINT);
  const [newId, setNewId] = useState("");
  const [versionEdit, setVersionEdit] = useState<string | null>(null);
  const [build, setBuild] = useState("");
  const [pasted, setPasted] = useState("");
  const [chosen, setChosen] = useState<string[]>([]);
  const [ownManifest, setOwnManifest] = useState("");
  const [publisherPick, setPublisherPick] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [formError, setFormError] = useState<string | null>(null);

  const id = which === NEW_BLUEPRINT ? newId.trim() : which;
  const version = versionEdit ?? nextVersion(releases.filter((b) => b.blueprint_id === id).map((b) => b.version));
  const publisher = publisherPick || me || "";
  const chosenTools = (tools.data?.items ?? []).filter((t) => chosen.includes(t.tool_id));

  // The manifest digest follows the tools ticked: the same tools always give the same digest.
  const digest = useQuery({
    queryKey: ["agents", "manifest-digest", ...chosenTools.map((t) => `${t.tool_id}@${t.version}`).sort()],
    queryFn: () => manifestDigest(chosenTools),
    enabled: chosenTools.length > 0,
    staleTime: Infinity,
  });
  const manifest = chosenTools.length > 0 ? (digest.data ?? "") : "";

  // The build: an earlier release's, a stable test placeholder, or one pasted. Only the test digest has to be worked out.
  const testDigest = useQuery({
    queryKey: ["agents", "test-build-digest", id, version],
    queryFn: () => testBuildDigest(id, version.trim()),
    enabled: build === BUILD_TEST && Boolean(id) && Boolean(version.trim()),
    staleTime: Infinity,
  });
  const artifact = digestFor(build, pasted, testDigest.data) ?? "";
  const enforced = builds.data?.enforced ?? false;
  const buildOptions = buildChoices({ builds: builds.data?.items ?? [], releases, enforced, allowTest: allowTestBuilds() });

  const manifestToSend = ownManifest.trim() || manifest;

  function close() {
    if (pending) return;
    setError(null);
    setFormError(null);
    onClose();
  }

  async function submit() {
    setFormError(null);
    setError(null);
    if (!/^[a-z0-9][a-z0-9._-]{1,62}$/.test(id)) return setFormError("Blueprint name: lowercase letters, digits, dot, dash or underscore (2 to 63 characters).");
    if (!version.trim()) return setFormError("Give the release a version.");
    if (releases.some((b) => b.blueprint_id === id && b.version === version.trim())) return setFormError(`${id} ${version} already exists. A release never changes: use the next version.`);
    if (!DIGEST_RE.test(artifact)) return setFormError("Choose which build it runs (or paste its sha256 digest).");
    if (!DIGEST_RE.test(manifestToSend)) return setFormError("Tick at least one tool for this release (or paste a tool manifest digest under Advanced).");
    if (!UUID_RE.test(publisher.trim())) return setFormError("Choose who publishes this release.");
    setPending(true);
    try {
      await protect(() =>
        api.agents.createBlueprint({
          blueprint_id: id,
          version: version.trim(),
          artifact_digest: artifact,
          tool_manifest_digest: manifestToSend,
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
        <AuthorityNotice needs={NEEDS.blueprint} what="publish a blueprint" />
        <div className="grid gap-4 sm:grid-cols-2">
          <Select
            label="Blueprint"
            isRequired
            value={which}
            onChange={(v) => {
              setWhich(v);
              setVersionEdit(null);
            }}
            options={[{ id: NEW_BLUEPRINT, label: "A new blueprint", description: "Start a new design." }, ...known.map((k) => ({ id: k, label: k, description: "A new version of this blueprint." }))]}
          />
          {which === NEW_BLUEPRINT ? (
            <TextField label="Name" isRequired mono value={newId} onChange={setNewId} placeholder="finance-approval-bot" description="Lowercase letters, digits, dot, dash." />
          ) : (
            <div />
          )}
        </div>
        <TextField label="Version" isRequired mono value={version} onChange={setVersionEdit} description="Suggested from the releases that exist. Change it only if you need a different number." />
        <Select
          label="Which build does it run?"
          isRequired
          placeholder="Choose…"
          value={build || null}
          onChange={setBuild}
          options={buildOptions}
          description={
            enforced
              ? "Only a build that has been registered and approved can be chosen. You do not need to know a digest."
              : "You do not need to know a digest: pick an approved build, a test build, or paste one from your registry."
          }
        />
        {enforced && buildOptions.length === 0 ? (
          <Banner tone="warn" title="No approved builds yet">
            Register the software under Builds; a second person approves it. Then it appears here to pick.
          </Banner>
        ) : null}
        {build === BUILD_PASTE ? (
          <TextField
            label="Build digest"
            isRequired
            mono
            value={pasted}
            onChange={setPasted}
            placeholder="sha256:…"
            description="The signed build or container digest, from your build pipeline or registry."
            errorMessage={pasted.trim() && !DIGEST_RE.test(pasted.trim()) ? "sha256: followed by 64 hex characters." : undefined}
          />
        ) : null}
        {build === BUILD_TEST ? (
          <Banner tone="warn" title="A test build is a placeholder">
            It lets you try the screens. A workload can get a token only if what is really running matches this digest, so before real use draft a new release with the real build.
          </Banner>
        ) : null}
        {artifact ? (
          <p className="text-xs text-ink-subtle">
            Build digest: <CopyId value={artifact} label="build digest" />
          </p>
        ) : null}
        <div className="space-y-2">
          <p className="text-sm font-medium text-ink">
            Tools in this release <span aria-hidden className="text-danger-fg">*</span>
          </p>
          <ToolChecklist
            tools={tools.data?.items ?? []}
            value={chosen}
            onChange={setChosen}
            emptyHint="No tools yet. Register one under Tools first; a release lists the tools its agents may ever use."
          />
          {manifest ? (
            <p className="text-xs text-ink-subtle">
              Tool manifest digest, worked out from these tools: <CopyId value={manifest} label="tool manifest digest" />
            </p>
          ) : null}
          <details>
            <summary className="cursor-pointer text-xs font-medium text-brand-700">Advanced: use my own tool manifest digest</summary>
            <div className="pt-2">
              <TextField label="Tool manifest digest" mono value={ownManifest} onChange={setOwnManifest} placeholder="sha256:…" description="Replaces the one worked out above." />
            </div>
          </details>
        </div>
        <PersonSelect label="Publisher" isRequired value={publisher} onChange={setPublisherPick} description="Who built and published this release. They cannot approve it." />
        {formError ? <Banner tone="danger" title="Check the form" live="polite">{formError}</Banner> : null}
        {error ? <ApiErrorView error={error} /> : null}
      </div>
    </Dialog>
  );
}
