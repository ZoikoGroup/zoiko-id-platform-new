"use client";

import type {
  Connector,
  ConnectorAuthProfile,
  ConnectorCapability,
  ConnectorType,
  LoginRoute,
  MappingRelease,
  MappingRule,
  RouteKind,
} from "@zoiko/api-client";
import {
  Banner,
  Button,
  ButtonLink,
  Card,
  Checkbox,
  Code,
  DataTable,
  Dialog,
  EmptyState,
  KeyValues,
  PageHeader,
  RelativeTime,
  Select,
  StatusBadge,
  Tabs,
  TextField,
  type Column,
} from "@zoiko/ui";
import { Cable, KeyRound, Plus, Route, ShieldAlert, Workflow } from "lucide-react";
import { useState } from "react";

import { ApiErrorView, QueryView, useCellMutation, useCellQuery } from "@/lib/cell";

import { ApprovalStep } from "./shared";
import { UserText } from "@/lib/user-text";

const CONNECTORS_KEY = ["integration", "connectors"];
const ROUTES_KEY = ["integration", "routes"];

const TYPES: { id: ConnectorType; label: string; prefix?: string }[] = [
  { id: "OIDC_IDP", label: "OpenID Connect identity provider", prefix: "federation_connection:" },
  { id: "SAML_IDP", label: "SAML identity provider", prefix: "federation_connection:" },
  { id: "SCIM", label: "SCIM directory", prefix: "provisioning_source:" },
  { id: "HRIS", label: "HR system (HRIS)", prefix: "provisioning_source:" },
  { id: "SIGNALS", label: "Shared signals (SSF)" },
  { id: "WEBHOOK", label: "Webhook" },
  { id: "DIRECTORY", label: "Directory lookup" },
];
const CAPABILITIES: { id: ConnectorCapability; label: string }[] = [
  { id: "AUTHN", label: "Sign-in" },
  { id: "PROVISIONING", label: "Provisioning" },
  { id: "GROUPS", label: "Groups" },
  { id: "HR_LIFECYCLE", label: "HR lifecycle" },
  { id: "SIGNALS", label: "Security signals" },
  { id: "WEBHOOK", label: "Webhooks" },
  { id: "DIRECTORY_LOOKUP", label: "Directory lookup" },
];
const AUTH: { id: ConnectorAuthProfile; label: string }[] = [
  { id: "OAUTH_CLIENT", label: "OAuth client" },
  { id: "MTLS", label: "Mutual TLS" },
  { id: "WORKLOAD_IDENTITY", label: "Workload identity" },
  { id: "SECRET", label: "Shared secret" },
];
const QUARANTINE_REASONS = [
  { id: "SUSPECTED_COMPROMISE", label: "Suspected compromise" },
  { id: "CONFIGURATION_DRIFT", label: "Configuration drift" },
  { id: "DATA_QUALITY", label: "Data quality problem" },
  { id: "MAINTENANCE", label: "Planned maintenance" },
];

function toggle<T>(list: T[], v: T): T[] {
  return list.includes(v) ? list.filter((x) => x !== v) : [...list, v];
}

/* ------------------------------------------------------------------------------- connectors */

function RegisterConnector({ onClose }: { onClose: () => void }) {
  const [type, setType] = useState<ConnectorType>("OIDC_IDP");
  const [name, setName] = useState("");
  const [source, setSource] = useState("");
  const [caps, setCaps] = useState<ConnectorCapability[]>(["AUTHN"]);
  const [auth, setAuth] = useState<ConnectorAuthProfile>("OAUTH_CLIENT");
  const [fresh, setFresh] = useState("");
  const prefix = TYPES.find((t) => t.id === type)?.prefix;
  const create = useCellMutation(
    (api) =>
      api.integration.createConnector({
        connector_type: type,
        display_name: name.trim(),
        source_ref: source.trim() || null,
        capabilities: caps,
        auth_profile: auth,
        freshness_minutes: fresh ? Number(fresh) : null,
      }),
    { invalidate: [CONNECTORS_KEY], success: "Connector registered", onSuccess: onClose },
  );
  const sourceOk = !prefix || source.trim().startsWith(prefix);
  const freshOk = !fresh || (/^\d+$/.test(fresh) && Number(fresh) >= 1 && Number(fresh) <= 10080);
  const valid = name.trim() && caps.length && sourceOk && freshOk;
  return (
    <Dialog
      isOpen
      onOpenChange={(o) => !o && !create.isPending && onClose()}
      title="Register a connector"
      description="A system Zoiko iD takes identity facts from: an identity provider, directory, HR system or signal stream."
      size="lg"
      footer={
        <>
          <Button variant="secondary" isDisabled={create.isPending} onPress={onClose}>
            Cancel
          </Button>
          <Button pending={create.isPending} isDisabled={!valid} onPress={() => create.mutate(undefined)}>
            Register
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <div className="grid gap-4 sm:grid-cols-2">
          <Select label="Type" value={type} onChange={(v) => setType(v as ConnectorType)} options={TYPES} />
          <TextField label="Name" isRequired value={name} onChange={setName} placeholder="Okta workforce" />
        </div>
        <TextField
          label="Source"
          mono
          isRequired={!!prefix}
          value={source}
          onChange={setSource}
          placeholder={prefix ? `${prefix}<id>` : "optional"}
          description={
            prefix ? (
              <>
                The {prefix === "federation_connection:" ? "identity provider connection" : "provisioning source"} it represents, as{" "}
                <Code>{`${prefix}<id>`}</Code>. Create it first under {prefix === "federation_connection:" ? "Identity providers" : "Provisioning"}.
              </>
            ) : undefined
          }
          errorMessage={source && !sourceOk ? `Must start with ${prefix}` : undefined}
        />
        <fieldset className="space-y-2">
          <legend className="text-sm font-medium text-ink">What it provides</legend>
          <div className="grid gap-2 sm:grid-cols-3">
            {CAPABILITIES.map((c) => (
              <Checkbox key={c.id} isSelected={caps.includes(c.id)} onChange={() => setCaps((l) => toggle(l, c.id))}>
                {c.label}
              </Checkbox>
            ))}
          </div>
        </fieldset>
        <div className="grid gap-4 sm:grid-cols-2">
          <Select label="How Zoiko iD authenticates to it" value={auth} onChange={(v) => setAuth(v as ConnectorAuthProfile)} options={AUTH} />
          <TextField
            label="Freshness (minutes)"
            inputMode="numeric"
            value={fresh}
            onChange={setFresh}
            description="How old its facts may be before high-risk decisions fail closed. Default by type."
            errorMessage={!freshOk ? "1 to 10080 minutes." : undefined}
          />
        </div>
        {create.error ? <ApiErrorView error={create.error} title="The connector was not registered" /> : null}
      </div>
    </Dialog>
  );
}

function CredentialDialog({ connector, onClose }: { connector: Connector; onClose: () => void }) {
  const [secret, setSecret] = useState("");
  const [notAfter, setNotAfter] = useState("");
  const save = useCellMutation(
    (api) => api.integration.setCredential(connector.connector_id, secret, notAfter ? new Date(notAfter).toISOString() : null),
    { invalidate: [CONNECTORS_KEY], success: (r) => `Credential version ${r.version} stored`, onSuccess: onClose },
  );
  return (
    <Dialog
      isOpen
      onOpenChange={(o) => !o && !save.isPending && onClose()}
      title={`Credential for ${connector.display_name}`}
      size="md"
      footer={
        <>
          <Button variant="secondary" isDisabled={save.isPending} onPress={onClose}>
            Cancel
          </Button>
          <Button pending={save.isPending} isDisabled={secret.length < 16} onPress={() => save.mutate(undefined)}>
            Store credential
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <Banner tone="info" icon={KeyRound}>
          Write-only: the value is sealed with the cell&apos;s data key and never shown again, not even to administrators. Storing a new one rotates
          it; the previous version keeps working for the overlap window.
        </Banner>
        <TextField label="Secret" type="password" isRequired mono value={secret} onChange={setSecret} autoComplete="off" description="At least 16 characters." />
        <TextField label="Expires" type="datetime-local" value={notAfter} onChange={setNotAfter} description="Optional. Expiry alerts start 30 days before." />
        {save.error ? <ApiErrorView error={save.error} title="The credential was not stored" /> : null}
      </div>
    </Dialog>
  );
}

function QuarantineDialog({ connector, onClose }: { connector: Connector; onClose: () => void }) {
  const [reason, setReason] = useState("SUSPECTED_COMPROMISE");
  const q = useCellMutation((api) => api.integration.quarantine(connector.connector_id, reason), {
    invalidate: [CONNECTORS_KEY],
    success: "Connector quarantined",
    onSuccess: onClose,
  });
  return (
    <Dialog
      isOpen
      onOpenChange={(o) => !o && !q.isPending && onClose()}
      title={`Quarantine ${connector.display_name}`}
      size="md"
      footer={
        <>
          <Button variant="secondary" isDisabled={q.isPending} onPress={onClose}>
            Cancel
          </Button>
          <Button variant="danger" pending={q.isPending} onPress={() => q.mutate(undefined)}>
            Quarantine
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <p className="text-sm text-ink-muted">
          Its facts stop being trusted at once: high-risk decisions that depend on it fail closed, and dependants follow its disable behaviour.
          Releasing it later needs a second administrator.
        </p>
        <Select label="Reason" value={reason} onChange={setReason} options={QUARANTINE_REASONS} />
        {q.error ? <ApiErrorView error={q.error} /> : null}
      </div>
    </Dialog>
  );
}

function ReleaseDialog({ connector, onClose }: { connector: Connector; onClose: () => void }) {
  const r = useCellMutation((api, id: string) => api.integration.release(connector.connector_id, id), {
    invalidate: [CONNECTORS_KEY],
    success: "Connector released",
    onSuccess: onClose,
  });
  return (
    <Dialog isOpen onOpenChange={(o) => !o && onClose()} title={`Release ${connector.display_name}`} size="lg" footer={<Button variant="secondary" onPress={onClose}>Close</Button>}>
      <ApprovalStep
        action="integration.connector.manage"
        resource={`integration_connector:${connector.connector_id}`}
        change="RELEASE"
        label="Release from quarantine"
        onRun={(id) => r.mutate(id)}
        pending={r.isPending}
        error={r.error}
      />
    </Dialog>
  );
}

function ConnectorsSection() {
  const connectors = useCellQuery(CONNECTORS_KEY, (api) => api.integration.connectors(), { refetchInterval: 30_000 });
  const [adding, setAdding] = useState(false);
  const [dialog, setDialog] = useState<{ kind: "credential" | "quarantine" | "release"; c: Connector } | null>(null);
  const columns: Column<Connector>[] = [
    { key: "name", header: "Connector", pinned: true, render: (c) => <span className="font-medium text-ink"><UserText>{c.display_name}</UserText></span>, sortValue: (c) => c.display_name },
    { key: "type", header: "Type", render: (c) => TYPES.find((t) => t.id === c.connector_type)?.label ?? c.connector_type },
    { key: "health", header: "Health", render: (c) => <StatusBadge state={c.health} kind="Connector health" size="xs" />, sortValue: (c) => c.health },
    { key: "fresh", header: "Fresh until", render: (c) => <RelativeTime value={c.fresh_until} /> },
    { key: "last", header: "Last success", render: (c) => <RelativeTime value={c.last_success_at} /> },
    { key: "failures", header: "Failures", align: "end", render: (c) => c.consecutive_failures },
    {
      key: "actions",
      header: "",
      align: "end",
      render: (c) => (
        <div className="flex justify-end gap-2">
          <Button size="sm" variant="secondary" onPress={() => setDialog({ kind: "credential", c })}>
            Credential
          </Button>
          {c.health === "QUARANTINED" ? (
            <Button size="sm" onPress={() => setDialog({ kind: "release", c })}>
              Release
            </Button>
          ) : (
            <Button size="sm" variant="ghost" onPress={() => setDialog({ kind: "quarantine", c })}>
              Quarantine
            </Button>
          )}
        </div>
      ),
    },
  ];
  return (
    <div className="space-y-4">
      <div className="flex justify-end">
        <Button icon={<Plus aria-hidden className="size-4" />} onPress={() => setAdding(true)}>
          Register connector
        </Button>
      </div>
      <Card padded={false}>
        <QueryView query={connectors}>
          {(r) =>
            r.items.length ? (
              <DataTable caption="Connectors" columns={columns} rows={r.items} rowKey={(c) => c.connector_id} />
            ) : (
              <EmptyState title="No connectors yet" icon={Cable}>
                Register the identity provider, directory or HR system your company already uses.
              </EmptyState>
            )
          }
        </QueryView>
      </Card>
      {adding ? <RegisterConnector onClose={() => setAdding(false)} /> : null}
      {dialog?.kind === "credential" ? <CredentialDialog connector={dialog.c} onClose={() => setDialog(null)} /> : null}
      {dialog?.kind === "quarantine" ? <QuarantineDialog connector={dialog.c} onClose={() => setDialog(null)} /> : null}
      {dialog?.kind === "release" ? <ReleaseDialog connector={dialog.c} onClose={() => setDialog(null)} /> : null}
    </div>
  );
}

/* --------------------------------------------------------------------------- mapping releases */

const EXAMPLE_RULES: MappingRule[] = [
  { source: "claims.department", target: "profile.department", transform: [{ op: "trim" }, { op: "lower" }], missing: "IGNORE", multi: "FIRST" },
  { source: "groups", target: "relation.member_of", transform: [{ op: "prefix_strip", prefix: "acme-" }], missing: "IGNORE", multi: "ALL" },
];
const EXAMPLE_RECORDS = [{ id: "sample-1", claims: { department: " Finance " }, groups: ["acme-payments"] }];

function parseJson<T>(text: string): { value?: T; error?: string } {
  try {
    return { value: JSON.parse(text) as T };
  } catch (e) {
    return { error: e instanceof Error ? e.message : "Not valid JSON." };
  }
}

function ReleaseDetail({ release }: { release: MappingRelease }) {
  const [records, setRecords] = useState(JSON.stringify(EXAMPLE_RECORDS, null, 2));
  const sim = useCellMutation((api, recs: Record<string, unknown>[]) => api.integration.simulate(release.release_id, recs), {
    invalidate: [["integration", "releases", release.connector_id]],
    success: "Simulated",
  });
  const activate = useCellMutation((api, id: string) => api.integration.activateRelease(release.release_id, id), {
    invalidate: [["integration", "releases", release.connector_id]],
    success: "Mapping release activated",
  });
  const parsed = parseJson<Record<string, unknown>[]>(records);
  const report = sim.data?.simulation_report ?? release.simulation_report;
  const clean = report && !report.conflicts && !report.errors;
  return (
    <div className="space-y-4">
      <KeyValues
        columns={3}
        items={[
          ["Version", `v${release.version}`],
          ["Status", <StatusBadge key="s" state={release.status} kind="Release" size="xs" />],
          ["Content hash", <Code key="h">{release.content_hash.slice(0, 16)}…</Code>],
        ]}
      />
      <details>
        <summary className="cursor-pointer text-sm font-medium text-ink">Rules ({release.rules.length})</summary>
        <pre className="mt-2 max-h-64 overflow-auto rounded-control bg-canvas p-3 text-xs">{JSON.stringify(release.rules, null, 2)}</pre>
      </details>
      {release.status !== "ACTIVE" && release.status !== "RETIRED" ? (
        <>
          <TextField
            label="Sample records to simulate (JSON array)"
            multiline
            rows={6}
            mono
            value={records}
            onChange={setRecords}
            errorMessage={parsed.error}
            description="Shaped like what the connector sends: claims, SCIM attributes, groups. Nothing is written."
          />
          <Button variant="secondary" pending={sim.isPending} isDisabled={!parsed.value} onPress={() => parsed.value && sim.mutate(parsed.value)}>
            Simulate
          </Button>
          {sim.error ? <ApiErrorView error={sim.error} title="Simulation failed" /> : null}
        </>
      ) : null}
      {report ? (
        <Card title="Simulation">
          <KeyValues
            columns={4}
            items={[
              ["Records", report.records],
              ["Mapped", report.mapped],
              ["Conflicts", report.conflicts],
              ["Errors", report.errors],
            ]}
          />
          {report.unknown_sources.length ? (
            <p className="mt-3 text-xs text-ink-muted">
              Unknown sources (drift): <Code>{report.unknown_sources.join(", ")}</Code>
            </p>
          ) : null}
        </Card>
      ) : null}
      {release.status !== "ACTIVE" && release.status !== "RETIRED" ? (
        clean ? (
          <ApprovalStep
            action="integration.mapping.manage"
            resource={`mapping_release:${release.release_id}`}
            change={`ACTIVATE:${release.content_hash}`}
            label="Activate release"
            onRun={(id) => activate.mutate(id)}
            pending={activate.isPending}
            error={activate.error}
          />
        ) : (
          <p className="text-sm text-ink-muted">Activation opens once a simulation has no conflicts and no errors.</p>
        )
      ) : null}
    </div>
  );
}

function MappingSection() {
  const connectors = useCellQuery(CONNECTORS_KEY, (api) => api.integration.connectors());
  const [connector, setConnector] = useState("");
  const chosen = connector || connectors.data?.items[0]?.connector_id || "";
  const releases = useCellQuery(["integration", "releases", chosen], (api) => api.integration.releases(chosen), { enabled: !!chosen });
  const [rules, setRules] = useState(JSON.stringify(EXAMPLE_RULES, null, 2));
  const [open, setOpen] = useState<string | null>(null);
  const [drafting, setDrafting] = useState(false);
  const parsed = parseJson<MappingRule[]>(rules);
  const create = useCellMutation((api, r: MappingRule[]) => api.integration.createRelease(chosen, r), {
    invalidate: [["integration", "releases", chosen]],
    success: (r) => `Release v${r.version} created`,
    onSuccess: (r) => {
      setDrafting(false);
      setOpen(r.release_id);
    },
  });
  if (connectors.data && !connectors.data.items.length) {
    return (
      <EmptyState title="Register a connector first" icon={Workflow}>
        Mapping releases translate a connector&apos;s claims, attributes and groups into profile facts and relationships.
      </EmptyState>
    );
  }
  const current = releases.data?.items.find((r) => r.release_id === open);
  return (
    <div className="space-y-4">
      <Banner tone="info" icon={ShieldAlert}>
        Mappings produce profile attributes, group relationships and role <em>candidates</em> only. A group never becomes signing authority through a
        mapping. Releases are immutable: change one by creating the next version; roll back by activating an earlier one.
      </Banner>
      <div className="flex flex-wrap items-end gap-3">
        <Select
          label="Connector"
          value={chosen}
          onChange={setConnector}
          options={(connectors.data?.items ?? []).map((c) => ({ id: c.connector_id, label: c.display_name }))}
          className="min-w-64"
        />
        <Button icon={<Plus aria-hidden className="size-4" />} isDisabled={!chosen} onPress={() => setDrafting(true)}>
          New release
        </Button>
      </div>
      <QueryView query={releases}>
        {(r) =>
          r.items.length ? (
            <ul className="divide-y divide-border rounded-card border border-border">
              {r.items.map((x) => (
                <li key={x.release_id} className="flex items-center justify-between gap-3 px-4 py-3">
                  <div className="flex items-center gap-3">
                    <span className="font-medium text-ink">v{x.version}</span>
                    <StatusBadge state={x.status} kind="Release" size="xs" />
                    <span className="text-xs text-ink-muted">{x.rules.length} rules</span>
                  </div>
                  <Button size="sm" variant="secondary" onPress={() => setOpen(x.release_id)}>
                    Open
                  </Button>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-sm text-ink-muted">No releases for this connector yet.</p>
          )
        }
      </QueryView>
      {drafting ? (
        <Dialog
          isOpen
          onOpenChange={(o) => !o && !create.isPending && setDrafting(false)}
          title="New mapping release"
          size="lg"
          footer={
            <>
              <Button variant="secondary" onPress={() => setDrafting(false)}>
                Cancel
              </Button>
              <Button pending={create.isPending} isDisabled={!parsed.value} onPress={() => parsed.value && create.mutate(parsed.value)}>
                Create release
              </Button>
            </>
          }
        >
          <div className="space-y-3">
            <p className="text-sm text-ink-muted">
              Each rule maps a <Code>source</Code> (<Code>claims.*</Code>, <Code>scim.*</Code> or <Code>groups</Code>) to a <Code>target</Code> (
              <Code>profile.*</Code>, <Code>relation.member_of</Code> or <Code>role_candidate</Code>) through fixed transforms: trim, lower, upper,
              map, prefix_strip, split, allow.
            </p>
            <TextField label="Rules (JSON array)" multiline rows={14} mono value={rules} onChange={setRules} errorMessage={parsed.error} />
            {create.error ? <ApiErrorView error={create.error} title="The release was not created" /> : null}
          </div>
        </Dialog>
      ) : null}
      {current ? (
        <Dialog isOpen onOpenChange={(o) => !o && setOpen(null)} title={`Mapping release v${current.version}`} size="lg" footer={<Button onPress={() => setOpen(null)}>Close</Button>}>
          <ReleaseDetail release={current} />
        </Dialog>
      ) : null}
    </div>
  );
}

/* ------------------------------------------------------------------------------ login routing */

function RoutingSection() {
  const routes = useCellQuery(ROUTES_KEY, (api) => api.integration.routes());
  const [kind, setKind] = useState<RouteKind>("DOMAIN");
  const [match, setMatch] = useState("");
  const [connection, setConnection] = useState("");
  const [probe, setProbe] = useState("");
  const add = useCellMutation(
    (api) => api.integration.addRoute({ kind, match: match.trim().toLowerCase(), connection_ref: connection.trim() }),
    {
      invalidate: [ROUTES_KEY],
      success: "Route added",
      onSuccess: () => {
        setMatch("");
        setConnection("");
      },
    },
  );
  const resolve = useCellMutation((api, d: string) => api.integration.resolveRoute({ login_domain: d }));
  const columns: Column<LoginRoute>[] = [
    { key: "kind", header: "When", render: (r) => (r.kind === "DOMAIN" ? "E-mail domain is" : "Application is") },
    { key: "match", header: "Match", render: (r) => <span className="font-mono text-sm">{r.match}</span>, sortValue: (r) => r.match },
    { key: "conn", header: "Send to", render: (r) => <span className="font-mono text-xs">{r.connection_ref}</span> },
  ];
  return (
    <div className="space-y-4">
      <p className="text-sm text-ink-muted">
        With several identity providers, routing decides which one a person signs in with: by the application they come from, else by their e-mail
        domain. People with no matching route sign in with their passkey.
      </p>
      <Card padded={false}>
        <QueryView query={routes}>
          {(r) =>
            r.items.length ? (
              <DataTable caption="Login routes" columns={columns} rows={r.items} rowKey={(x) => x.rule_id} />
            ) : (
              <EmptyState title="No routes" icon={Route} compact>
                Everyone signs in with their passkey.
              </EmptyState>
            )
          }
        </QueryView>
      </Card>
      <Card title="Add a route">
        <div className="grid gap-3 sm:grid-cols-[12rem_1fr_1fr_auto] sm:items-end">
          <Select
            label="When"
            value={kind}
            onChange={(v) => setKind(v as RouteKind)}
            options={[
              { id: "DOMAIN", label: "E-mail domain" },
              { id: "APPLICATION", label: "Application" },
            ]}
          />
          <TextField label={kind === "DOMAIN" ? "Domain" : "Application ID"} mono value={match} onChange={setMatch} placeholder={kind === "DOMAIN" ? "example.com" : ""} />
          <TextField label="Identity provider connection" mono value={connection} onChange={setConnection} placeholder="federation connection id" />
          <Button pending={add.isPending} isDisabled={!match.trim() || connection.trim().length < 10} onPress={() => add.mutate(undefined)}>
            Add
          </Button>
        </div>
        {add.error ? <div className="mt-3"><ApiErrorView error={add.error} title="The route was not added" /></div> : null}
      </Card>
      <Card title="Test routing">
        <div className="flex flex-wrap items-end gap-3">
          <TextField label="E-mail domain" mono value={probe} onChange={setProbe} placeholder="example.com" className="min-w-64" />
          <Button variant="secondary" pending={resolve.isPending} isDisabled={!probe.trim()} onPress={() => resolve.mutate(probe.trim().toLowerCase())}>
            Resolve
          </Button>
        </div>
        {resolve.data ? (
          <p className="mt-3 text-sm text-ink">
            {resolve.data.connection_ref ? (
              <>
                Signs in with <Code>{resolve.data.connection_ref}</Code>
              </>
            ) : (
              "No route: signs in with a passkey."
            )}
          </p>
        ) : null}
        {resolve.error ? <ApiErrorView error={resolve.error} /> : null}
      </Card>
    </div>
  );
}

/** Integrations (UX-001 §15; OVERLAY-001 §11, §14-15, §24): connectors and their health, write-only
 * credentials, immutable mapping releases with simulation, and multi-IdP login routing. */
export function IntegrationsPage() {
  return (
    <div className="space-y-6">
      <PageHeader
        icon={Cable}
        title="Integrations"
        description="The systems your company already runs and how Zoiko iD uses them: connectors, attribute mappings and sign-in routing."
        breadcrumbs={[{ label: "Onboarding", href: "/admin/onboarding" }, { label: "Integrations" }]}
        actions={
          <ButtonLink href="/admin/applications" variant="secondary">
            Applications
          </ButtonLink>
        }
      />
      <Tabs
        label="Integrations"
        tabs={[
          { id: "connectors", label: "Connectors", content: <ConnectorsSection /> },
          { id: "mappings", label: "Mappings", content: <MappingSection /> },
          { id: "routing", label: "Sign-in routing", content: <RoutingSection /> },
        ]}
      />
    </div>
  );
}
