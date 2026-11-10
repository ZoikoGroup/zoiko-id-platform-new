"use client";

import type { BuildItem } from "@zoiko/api-client";
import { useQueryClient } from "@tanstack/react-query";
import { Banner, Button, Card, CopyId, DataTable, DateTime, Dialog, EmptyState, Select, StatusBadge, TextField, useToast, type Column } from "@zoiko/ui";
import { CheckCircle2, Loader2, PackageCheck, Plus } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { ApiErrorView, QueryView, useCellMutation, useCellQuery } from "@/lib/cell";
import { useRuntime } from "@/lib/runtime";

import { PersonLabel, usePeople } from "../team/person-label";
import { ApprovalQueue, PipelineGuide } from "./build-review";
import { NEEDS, knownRepositories, parseImage, sortVersions, worthLookingUp, type ParsedImage } from "./agents-logic";
import { AuthorityNotice, DigestHelp } from "./pickers";
import { AGENTS_KEY, BLUEPRINTS_KEY, BUILDS_KEY, DIGEST_RE } from "./shared";

/**
 * The reviewed software a blueprint release may run. Whoever builds the software registers its digest here once; a second person
 * approves it; everyone else then simply picks it when drafting a release. Registering takes the image name: the digest is looked up, not typed.
 */
export function BuildsSection() {
  const builds = useCellQuery(BUILDS_KEY, (api) => api.agents.builds());
  const person = usePeople();
  const { session } = useRuntime();
  const me = session?.authenticated ? session.subject.sub : undefined;
  const apiBase = session?.authenticated ? session.apiBase : undefined;
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
      <div className="space-y-4">
        <ApprovalQueue
          builds={builds.data?.items ?? []}
          person={person}
          approve={(b) => approve.mutate(b)}
          approvingId={approve.isPending ? approve.variables?.build_id : undefined}
          me={me}
        />
        {approve.error ? <ApiErrorView error={approve.error} /> : null}
      <Card
        padded={false}
        title="All builds"
        description="The software an agent may run. A build pipeline registers each one; a second person approves it; then anyone drafting a blueprint release picks it from a list."
        actions={
          <Button size="sm" variant="secondary" icon={<Plus aria-hidden className="size-4" />} onPress={() => setCreating(true)}>
            Register by hand
          </Button>
        }
      >
        <QueryView query={builds}>
          {(data) => (
            <>
              <div className="space-y-3 p-4 pb-0">
                <AuthorityNotice needs={[...NEEDS.build, ...NEEDS.approveBuild]} what="register or approve a build" />
                <Banner tone={data.enforced ? "info" : "warn"} title={data.enforced ? "Releases must run an approved build" : "Releases may run any digest in this environment"}>
                  {data.enforced
                    ? "A blueprint release is refused unless its build is approved here."
                    : "Approved builds are not required here (a local, test or development setup). A deployed environment requires them."}
                </Banner>
              </div>
              <DataTable
                caption="Approved builds"
                columns={columns}
                rows={data.items}
                rowKey={(b) => b.build_id}
                empty={
                  <EmptyState icon={PackageCheck} title="No builds registered">
                    Let your build pipeline register builds (see below): nobody types anything, and a second person approves each one. Or press Register by hand to pick or paste an image yourself.
                  </EmptyState>
                }
              />
            </>
          )}
        </QueryView>
      </Card>
        <PipelineGuide hasBuilds={(builds.data?.items.length ?? 0) > 0} apiBase={apiBase} />
      </div>

      <RegisterBuildDialog
        isOpen={creating}
        onClose={() => setCreating(false)}
        lookupEnabled={builds.data?.lookup_enabled ?? false}
        repositories={knownRepositories(builds.data?.images ?? [], builds.data?.items ?? [])}
      />

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

/**
 * Registering a build takes no typing where the console knows the software: pick it from a list, pick a version, and the console asks the registry
 * for the digest on its own, suggests the build's name and records where it came from. Where it does not know the software yet, the person pastes
 * `ghcr.io/acme/invoice-bot:1.4` once and it is offered from then on. A name pinned to its digest (`name@sha256:…`, as `docker inspect`
 * prints it) needs no lookup at all. Typing a digest by hand is the fallback, for where lookup is not switched on or cannot reach the image.
 */
const OTHER_IMAGE = "__other__";

export function RegisterBuildDialog({ isOpen, onClose, lookupEnabled, repositories = [] }: { isOpen: boolean; onClose: () => void; lookupEnabled: boolean; repositories?: string[] }) {
  const { api, protect } = useRuntime();
  const qc = useQueryClient();
  const toast = useToast();
  const [image, setImage] = useState("");
  const [repo, setRepo] = useState("");
  const [tag, setTag] = useState("");
  const [name, setName] = useState("");
  const [nameTouched, setNameTouched] = useState(false);
  const [digest, setDigest] = useState("");
  const [provenance, setProvenance] = useState("");
  const [found, setFound] = useState<string | null>(null);
  const [manual, setManual] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const current = useRef("");
  useEffect(() => {
    current.current = image.trim();
  }, [image]);

  // Asking changes nothing: it fills the digest in from what the registry says the name points at, and the person still registers it.
  const lookup = useCellMutation((api, asked: string) => api.agents.resolveBuild(asked), {
    onSuccess: (r, asked) => {
      if (asked !== undefined && asked !== current.current) return; // the person has typed on since: this answer is for another name
      setDigest(r.digest);
      setFound(`${r.registry}/${r.repository}:${r.reference}`);
    },
  });

  // Where the console knows some software, a person picks it and a version; "Another image" (or nothing known yet) is the typed field.
  const picking = lookupEnabled && repositories.length > 0 && repo !== OTHER_IMAGE;
  // With only one piece of software known there is nothing to choose: it is already chosen.
  const chosen = repo || (picking && repositories.length === 1 ? (repositories[0] ?? "") : "");
  const versions = useCellQuery(["build-versions", chosen], (api) => api.agents.buildVersions(chosen), { enabled: isOpen && picking && chosen !== "" });
  const tags = sortVersions(versions.data?.tags ?? []);
  const parsed = parseImage(image);
  const failed = lookup.error != null;
  // A team name or a plain word is not an image: say so as it is typed, instead of failing later with a digest box nobody understands.
  const notAnImage = image.trim() !== "" && !parsed?.digest && !worthLookingUp(image);
  // Where lookup is on nobody is asked for a digest, unless the registry could not say or they choose to. Where it is off, a bare name needs one.
  const needsDigest = !parsed?.digest && (!lookupEnabled || failed || manual);

  // Look the name up on its own once it is shaped like an image, a moment after the person stops typing.
  useEffect(() => {
    if (!lookupEnabled || !parsed || parsed.digest || !worthLookingUp(parsed.image)) return;
    const asked = parsed.image;
    const timer = setTimeout(() => lookup.mutate(asked), 700);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- re-run only when the text itself changes (mutate is stable)
  }, [image, lookupEnabled]);

  function changeImage(v: string) {
    const p = parseImage(v);
    setImage(v);
    setFound(null);
    setDigest(p?.digest ?? "");
    if (!nameTouched) setName(p?.name ?? "");
    setFormError(null);
  }

  function chooseRepo(v: string) {
    setRepo(v);
    setTag("");
    changeImage("");
  }

  function chooseTag(t: string) {
    setTag(t);
    changeImage(`${chosen}:${t}`);
  }

  function close() {
    if (pending) return;
    setError(null);
    setFormError(null);
    onClose();
  }

  async function submit() {
    setFormError(null);
    setError(null);
    const p = parseImage(image);
    if (!p && !digest.trim()) return setFormError(picking ? "Choose the software and its version." : lookupEnabled ? "Paste the image name, for example ghcr.io/acme/invoice-bot:1.4." : "Paste the image name with its digest, or the digest itself below.");
    setPending(true);
    try {
      let artifact = digest.trim() || p?.digest || "";
      let from = found ?? p?.image ?? "";
      if (lookupEnabled && !artifact && p && worthLookingUp(p.image)) {
        // The person did not wait for the lookup: work the digest out now, from the registry, exactly as the automatic lookup would.
        const r = await protect(() => api.agents.resolveBuild(p.image));
        artifact = r.digest;
        from = `${r.registry}/${r.repository}:${r.reference}`;
        setDigest(artifact);
        setFound(from);
      }
      if (!DIGEST_RE.test(artifact)) {
        setManual(true);
        setFormError(lookupEnabled ? "Paste the image name so its digest can be looked up, or enter the digest under More options." : "The digest must be sha256: followed by 64 hex characters.");
        return;
      }
      await protect(() =>
        api.agents.registerBuild({
          name: name.trim() || p?.name || "Build",
          artifact_digest: artifact,
          source_ref: from || null,
          provenance_ref: provenance.trim() || null,
        }),
      );
      await qc.invalidateQueries({ queryKey: BUILDS_KEY });
      toast.notify({ tone: "ok", title: "Build registered", body: "Someone other than you must approve it before a release can use it." });
      setImage("");
      setRepo("");
      setTag("");
      setName("");
      setNameTouched(false);
      setDigest("");
      setProvenance("");
      setFound(null);
      setManual(false);
      onClose();
    } catch (e) {
      setError(e);
      if (lookupEnabled) setManual(true);
    } finally {
      setPending(false);
    }
  }

  return (
    <Dialog
      isOpen={isOpen}
      onOpenChange={(o) => !o && close()}
      title="Register build"
      description={picking ? "Choose the software the agent will run. The rest is filled in for you, and a second person approves it before it can be used." : "Paste the image the agent will run. The rest is filled in for you, and a second person approves it before it can be used."}
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
        {picking ? (
          <>
            <Select
              label="Software"
              isRequired
              placeholder="Choose…"
              value={chosen || null}
              onChange={chooseRepo}
              options={[...repositories.map((r) => ({ id: r, label: r })), { id: OTHER_IMAGE, label: "Another image…", description: "Paste its name once; it is offered here afterwards." }]}
              description="The software already registered, or listed by your administrator."
            />
            {chosen !== "" ? (
              <Select
                label="Version"
                isRequired
                placeholder={versions.isPending ? "Loading versions…" : tags.length ? "Choose…" : "No versions found"}
                value={tag || null}
                onChange={chooseTag}
                isDisabled={versions.isPending || tags.length === 0}
                options={tags.map((t) => ({ id: t, label: t, description: t === "latest" ? "A moving tag: what gets registered is the digest it points at today." : undefined }))}
                description="Newest first. The digest is looked up for the version you choose."
                errorMessage={versions.error ? "The versions could not be listed. Choose Another image and paste its name." : undefined}
              />
            ) : null}
          </>
        ) : (
          <>
            {lookupEnabled && repositories.length === 0 ? (
              <Banner tone="info" title="No software is listed yet">
                Paste the image name once and it is offered as a list from then on. Your administrator can list software ahead of time (ZID_BUILD_IMAGES), and a build
                pipeline can register builds with nobody typing anything: you then only approve them here.
              </Banner>
            ) : null}
            <TextField
              label="Image"
              isRequired
              mono
              value={image}
              onChange={changeImage}
              placeholder="ghcr.io/acme/invoice-bot:1.4"
              description={
                lookupEnabled
                  ? "The image name from your registry or pipeline. You do not type a digest: it is looked up for you."
                  : "The image name with its digest as Docker prints it (name@sha256:…), or just the name and the digest below."
              }
              errorMessage={notAnImage ? "That is not an image name. It looks like ghcr.io/acme/invoice-bot:1.4 (a local image: acme/invoice-agent:1.0.0)." : undefined}
            />
            {lookupEnabled && repositories.length > 0 ? (
              <Button size="xs" variant="secondary" onPress={() => { setRepo(""); setTag(""); changeImage(""); }}>
                Pick from a list instead
              </Button>
            ) : null}
          </>
        )}
        <ImageStatus lookupEnabled={lookupEnabled} parsed={parsed} digest={digest} found={found} checking={lookup.isPending} failed={failed} />
        {lookupEnabled && parsed && !parsed.digest && !digest && worthLookingUp(parsed.image) ? (
          <Button size="sm" variant="secondary" pending={lookup.isPending} onPress={() => lookup.mutate(parsed.image)}>
            Look up digest
          </Button>
        ) : null}
        <TextField label="Name" value={name} onChange={(v) => { setName(v); setNameTouched(true); }} description="Filled in from the image so people recognise it in a list. Change it if you like." />
        {lookup.error ? <ApiErrorView error={lookup.error} title="The digest could not be looked up" /> : null}
        {needsDigest ? (
          <>
            <TextField
              label="Build digest"
              isRequired={!lookupEnabled}
              mono
              value={digest}
              onChange={setDigest}
              placeholder="sha256:…"
              description={
                lookupEnabled
                  ? "Only because the registry could not say (a private image, say): the digest your build pipeline or registry shows."
                  : "Lookup by image name is not switched on here. Paste it from your registry or pipeline, or let the build pipeline register builds (tools/scripts/register_build.py)."
              }
              errorMessage={digest.trim() && !DIGEST_RE.test(digest.trim()) ? "sha256: followed by 64 hex characters." : undefined}
            />
            <DigestHelp canUseTestBuild={false} />
          </>
        ) : null}
        <details className="rounded-card border border-border p-3 text-sm" open={manual && lookupEnabled ? true : undefined}>
          <summary className="cursor-pointer font-medium text-ink">More options</summary>
          <div className="mt-3 space-y-3">
            <TextField label="Proof of how it was built (optional)" mono value={provenance} onChange={setProvenance} placeholder="https://…" description="A link to the build's provenance or signature. It is recorded, not verified." />
            {lookupEnabled && !needsDigest ? (
              <Button size="xs" variant="secondary" onPress={() => setManual(true)}>
                Enter a digest by hand instead
              </Button>
            ) : null}
          </div>
        </details>
        {formError ? <Banner tone="danger" title="Check the form" live="polite">{formError}</Banner> : null}
        {error ? <ApiErrorView error={error} /> : null}
      </div>
    </Dialog>
  );
}

/** One line saying where the digest stands, so nobody wonders whether anything happened. */
function ImageStatus({ lookupEnabled, parsed, digest, found, checking, failed }: { lookupEnabled: boolean; parsed: ParsedImage | null; digest: string; found: string | null; checking: boolean; failed: boolean }) {
  if (!parsed || failed) return null;
  if (parsed.digest)
    return (
      <p className="flex items-start gap-2 text-xs text-ink-muted">
        <CheckCircle2 aria-hidden className="mt-0.5 size-4 shrink-0 text-success-fg" />
        <span>
          Digest taken from the name: <span className="break-all font-mono">{parsed.digest}</span>
        </span>
      </p>
    );
  if (checking)
    return (
      <p className="flex items-center gap-2 text-xs text-ink-muted" role="status">
        <Loader2 aria-hidden className="size-4 animate-spin" />
        Checking the registry…
      </p>
    );
  if (found && digest)
    return (
      <p className="flex items-start gap-2 text-xs text-ink-muted" role="status">
        <CheckCircle2 aria-hidden className="mt-0.5 size-4 shrink-0 text-success-fg" />
        <span>
          Found <span className="font-mono">{found}</span>. Digest: <span className="break-all font-mono">{digest}</span>. A tag can move: the digest is what gets registered and
          approved, so it never changes under you.
        </span>
      </p>
    );
  return lookupEnabled ? null : <p className="text-xs text-ink-muted">Lookup by image name is not switched on here.</p>;
}
