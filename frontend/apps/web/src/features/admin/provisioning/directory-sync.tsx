"use client";

import type { DirectorySync, ProvisioningSourceItem } from "@zoiko/api-client";
import { Banner, Button, Card, DataTable, DateTime, Dialog, EmptyState, RiskBadge, Select, StatusBadge, TextField, type Column } from "@zoiko/ui";
import { KeyRound, Pause, Play, Plug, RefreshCw } from "lucide-react";
import { useState } from "react";

import { ApiErrorView, QueryView, useCellMutation, useCellQuery } from "@/lib/cell";

import { SOURCES_KEY, SourceError } from "./create-source-dialog";
import { INTERVALS, countsSummary, isAuth0Domain, syncErrorMessage } from "./directory-sync-logic";
import { UserText } from "@/lib/user-text";

export const SYNCS_KEY = ["admin", "provisioning", "directory-syncs"];

/** Connect a company's Auth0 to one of its provisioning sources. The secret is typed once, sealed by the cell and never shown again. */
function ConnectDialog({ isOpen, onClose, sources }: { isOpen: boolean; onClose: () => void; sources: ProvisioningSourceItem[] }) {
  const active = sources.filter((s) => s.status === "ACTIVE");
  const [name, setName] = useState("Auth0");
  const [sourceId, setSourceId] = useState<string | null>(null);
  const [domain, setDomain] = useState("");
  const [clientId, setClientId] = useState("");
  const [secret, setSecret] = useState("");
  const [interval, setInterval] = useState("3600");
  const [approval, setApproval] = useState("");

  const create = useCellMutation(
    (api, body: Parameters<typeof api.provisioning.createDirectorySync>[0]) => api.provisioning.createDirectorySync(body),
    { invalidate: [SYNCS_KEY], success: "Directory connected", onSuccess: () => close(true) },
  );

  function close(force = false) {
    if (create.isPending && !force) return;
    setName("Auth0");
    setSourceId(null);
    setDomain("");
    setClientId("");
    setSecret("");
    setInterval("3600");
    setApproval("");
    create.reset();
    onClose();
  }

  const domainBad = domain.trim() !== "" && !isAuth0Domain(domain);
  const chosen = sourceId ?? (active.length === 1 ? active[0]!.source_id : null);
  const valid = name.trim() !== "" && chosen !== null && isAuth0Domain(domain) && clientId.trim().length >= 8 && secret.length > 0;

  return (
    <Dialog
      isOpen={isOpen}
      onOpenChange={(o) => !o && close()}
      title="Connect Auth0"
      description="Zoiko iD reads your Auth0 users on a schedule and keeps one of your provisioning sources in step with them."
      size="lg"
      footer={
        <>
          <Button variant="secondary" isDisabled={create.isPending} onPress={() => close()}>
            Cancel
          </Button>
          <Button
            pending={create.isPending}
            isDisabled={!valid}
            onPress={() =>
              chosen &&
              create.mutate({
                source_id: chosen,
                provider: "AUTH0",
                display_name: name.trim(),
                settings: { domain: domain.trim().toLowerCase(), client_id: clientId.trim() },
                secret,
                interval_seconds: Number(interval),
                approval_request_id: approval.trim() || null,
              })
            }
          >
            Connect
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <RiskBadge risk="R3" />
        {active.length === 0 ? (
          <Banner tone="warn" title="Register a provisioning source first">
            A directory sync feeds one provisioning source. Register one above, then connect Auth0 to it.
          </Banner>
        ) : null}
        <Banner tone="info" title="Use a read-only Auth0 application">
          Create a Machine to Machine application in Auth0 and authorize it for the Auth0 Management API with <strong>only the read:users permission</strong>. A
          secret with anything more is refused.
        </Banner>
        <TextField label="Name" isRequired value={name} onChange={setName} description="What your team will call this connection." />
        <Select
          label="Provisioning source"
          isRequired
          value={chosen}
          onChange={setSourceId}
          options={active.map((s) => ({ id: s.source_id, label: s.display_name }))}
          placeholder="Select…"
        />
        <TextField
          label="Auth0 domain"
          isRequired
          mono
          value={domain}
          onChange={setDomain}
          placeholder="your-tenant.us.auth0.com"
          errorMessage={domainBad ? "Use your Auth0 tenant address, ending in .auth0.com." : undefined}
        />
        <TextField label="Client ID" isRequired mono value={clientId} onChange={setClientId} description="Of the read-only Machine to Machine application." />
        <TextField
          label="Client secret"
          isRequired
          mono
          type="password"
          value={secret}
          onChange={setSecret}
          autoComplete="off"
          description="Sealed as soon as you connect and never shown again. You can replace it later."
        />
        <Select
          label="How often"
          value={interval}
          onChange={(v) => v && setInterval(v)}
          options={INTERVALS.map((i) => ({ id: i.id, label: i.label }))}
        />
        <TextField
          label="Approval request ID (optional)"
          mono
          value={approval}
          onChange={setApproval}
          description="Needed when your tenant policy requires a second administrator."
        />
        {create.error ? <SourceError error={create.error} /> : null}
      </div>
    </Dialog>
  );
}

function SecretDialog({ sync, onClose }: { sync: DirectorySync | null; onClose: () => void }) {
  const [secret, setSecret] = useState("");
  const set = useCellMutation((api, v: { id: string; secret: string }) => api.provisioning.setDirectorySyncSecret(v.id, v.secret), {
    invalidate: [SYNCS_KEY],
    success: "Secret replaced",
    onSuccess: () => close(true),
  });
  function close(force = false) {
    if (set.isPending && !force) return;
    setSecret("");
    set.reset();
    onClose();
  }
  return (
    <Dialog
      isOpen={sync !== null}
      onOpenChange={(o) => !o && close()}
      title="Replace the client secret"
      size="md"
      footer={
        <>
          <Button variant="secondary" isDisabled={set.isPending} onPress={() => close()}>
            Cancel
          </Button>
          <Button pending={set.isPending} isDisabled={!secret} onPress={() => sync && set.mutate({ id: sync.sync_id, secret })}>
            Replace secret
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <RiskBadge risk="R3" />
        <p className="text-sm text-ink-muted">
          The old secret stops working for <strong className="text-ink"><UserText>{sync?.display_name}</UserText></strong> at once. Use this after you rotate the secret in Auth0.
        </p>
        <TextField label="New client secret" isRequired mono type="password" value={secret} onChange={setSecret} autoComplete="off" />
        {set.error ? <ApiErrorView error={set.error} /> : null}
      </div>
    </Dialog>
  );
}

export function DirectorySyncSection({ sources }: { sources: ProvisioningSourceItem[] }) {
  const syncs = useCellQuery(SYNCS_KEY, (api) => api.provisioning.directorySyncs());
  const [connecting, setConnecting] = useState(false);
  const [replacing, setReplacing] = useState<DirectorySync | null>(null);
  const sourceName = (id: string) => sources.find((s) => s.source_id === id)?.display_name ?? id.slice(0, 8);

  const refresh = { invalidate: [SYNCS_KEY, SOURCES_KEY] };
  const run = useCellMutation((api, id: string) => api.provisioning.runDirectorySync(id), { ...refresh, success: "Queued: it runs within a few seconds" });
  const pause = useCellMutation((api, id: string) => api.provisioning.pauseDirectorySync(id), { ...refresh, success: "Paused" });
  const resume = useCellMutation((api, id: string) => api.provisioning.resumeDirectorySync(id), { ...refresh, success: "Resumed" });

  const columns: Column<DirectorySync>[] = [
    { key: "name", header: "Directory", pinned: true, render: (s) => <span className="font-medium"><UserText>{s.display_name}</UserText></span>, sortValue: (s) => s.display_name },
    { key: "domain", header: "Auth0 tenant", render: (s) => <span className="font-mono text-xs">{s.settings.domain}</span> },
    { key: "source", header: "Feeds", render: (s) => sourceName(s.source_id) },
    { key: "status", header: "Status", render: (s) => <StatusBadge state={s.status} kind="Sync status" /> },
    {
      key: "last",
      header: "Last run",
      render: (s) =>
        s.last_run_at ? (
          <div className="space-y-0.5">
            <div className="text-xs">
              <DateTime value={s.last_run_at} /> · {s.last_outcome === "OK" ? countsSummary(s.last_counts) : "failed"}
            </div>
            {syncErrorMessage(s.last_error) ? <div className="max-w-md text-xs text-ink-muted">{syncErrorMessage(s.last_error)}</div> : null}
          </div>
        ) : (
          <span className="text-xs text-ink-muted">Not run yet</span>
        ),
    },
    {
      key: "actions",
      header: "Actions",
      align: "end",
      render: (s) => (
        <div className="flex flex-wrap justify-end gap-2">
          {s.status === "ACTIVE" ? (
            <>
              <Button size="xs" variant="secondary" icon={<RefreshCw aria-hidden className="size-3.5" />} isDisabled={run.isPending} onPress={() => run.mutate(s.sync_id)}>
                Run now
              </Button>
              <Button size="xs" variant="secondary" icon={<Pause aria-hidden className="size-3.5" />} isDisabled={pause.isPending} onPress={() => pause.mutate(s.sync_id)}>
                Pause
              </Button>
            </>
          ) : (
            <Button size="xs" variant="secondary" icon={<Play aria-hidden className="size-3.5" />} isDisabled={resume.isPending} onPress={() => resume.mutate(s.sync_id)}>
              Resume
            </Button>
          )}
          <Button size="xs" variant="secondary" icon={<KeyRound aria-hidden className="size-3.5" />} onPress={() => setReplacing(s)}>
            Replace secret
          </Button>
        </div>
      ),
    },
  ];

  return (
    <>
      <Card
        title="Directory sync"
        description="Keep a provisioning source in step with your company's own directory. Zoiko iD reads it on a schedule; people it lists are provisioned, and people it no longer lists are deactivated."
        actions={
          <Button icon={<Plug aria-hidden className="size-4" />} onPress={() => setConnecting(true)}>
            Connect Auth0
          </Button>
        }
        padded={false}
      >
        <QueryView query={syncs}>
          {(data) => (
            <DataTable
              caption="Directory syncs"
              columns={columns}
              rows={data.items}
              rowKey={(s) => s.sync_id}
              empty={
                <EmptyState icon={Plug} title="No directory connected">
                  Connect Auth0 so your people are provisioned without anyone sending them by hand.
                </EmptyState>
              }
            />
          )}
        </QueryView>
        {run.error || pause.error || resume.error ? (
          <div className="p-4">
            <ApiErrorView error={run.error ?? pause.error ?? resume.error} />
          </div>
        ) : null}
      </Card>
      <ConnectDialog isOpen={connecting} onClose={() => setConnecting(false)} sources={sources} />
      <SecretDialog sync={replacing} onClose={() => setReplacing(null)} />
    </>
  );
}
