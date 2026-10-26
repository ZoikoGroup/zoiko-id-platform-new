"use client";

import type { AppEnvironment, AppProtocol, AppStatus, IntegrationApplication, PepContextField, PepObligation } from "@zoiko/api-client";
import { Button, Card, Checkbox, DataTable, Dialog, EmptyState, PageHeader, Select, StatusBadge, TextField, type Column } from "@zoiko/ui";
import { AppWindow, Plus } from "lucide-react";
import { useState } from "react";

import { ApiErrorView, QueryView, useCellMutation, useCellQuery } from "@/lib/cell";

import { CLIENTS_KEY, OAuthClientsCard } from "./oauth-clients";
import { GATES_KEY } from "./shared";

export const APPS_KEY = ["integration", "applications"];

const ENVIRONMENTS: { id: AppEnvironment; label: string }[] = [
  { id: "PROD", label: "Production" },
  { id: "STAGE", label: "Staging" },
  { id: "TEST", label: "Test" },
  { id: "DEV", label: "Development" },
];
const PROTOCOLS: { id: AppProtocol; label: string }[] = [
  { id: "AUTHZEN", label: "AuthZEN (access decisions)" },
  { id: "OIDC", label: "OpenID Connect" },
  { id: "SAML", label: "SAML 2.0" },
  { id: "SCIM", label: "SCIM provisioning" },
  { id: "API", label: "API" },
  { id: "WEBHOOK", label: "Webhooks" },
];
const OBLIGATIONS: { id: PepObligation; label: string }[] = [
  { id: "STEP_UP", label: "Step-up authentication" },
  { id: "HUMAN_APPROVAL", label: "Human approval" },
  { id: "COMMIT_RECHECK", label: "Re-check at commit" },
  { id: "MANDATE_RESERVE", label: "Mandate reservation" },
  { id: "TOOL_ALLOWLIST", label: "Tool allow-list" },
];
const CONTEXT: { id: PepContextField; label: string }[] = [
  { id: "purpose", label: "Purpose" },
  { id: "represented_principal_id", label: "Represented party" },
  { id: "mandate_id", label: "Mandate" },
  { id: "transaction_context_id", label: "Transaction context" },
  { id: "tool_id", label: "Tool" },
  { id: "value", label: "Value" },
];

/** The next lifecycle moves offered for an application (integration domain APP_TRANSITIONS). */
const NEXT: Partial<Record<AppStatus, { to: AppStatus; label: string }[]>> = {
  DRAFT: [{ to: "TESTING", label: "Start testing" }],
  TESTING: [{ to: "ACTIVE", label: "Activate" }, { to: "DRAFT", label: "Back to draft" }],
  ACTIVE: [{ to: "SUSPENDED", label: "Suspend" }],
  SUSPENDED: [{ to: "ACTIVE", label: "Resume" }],
};

function toggle<T>(list: T[], v: T): T[] {
  return list.includes(v) ? list.filter((x) => x !== v) : [...list, v];
}

function RegisterApp({ onClose }: { onClose: () => void }) {
  const [name, setName] = useState("");
  const [environment, setEnvironment] = useState<AppEnvironment>("PROD");
  const [protocols, setProtocols] = useState<AppProtocol[]>(["AUTHZEN"]);
  const [pep, setPep] = useState("");
  const [obligations, setObligations] = useState<PepObligation[]>(["STEP_UP"]);
  const [context, setContext] = useState<PepContextField[]>(["purpose"]);
  const clients = useCellQuery(CLIENTS_KEY, (api) => api.integration.oauthClients());
  const mine = (clients.data?.items ?? []).filter((c) => c.status === "ACTIVE" && !c.first_party);
  const create = useCellMutation(
    (api) =>
      api.integration.createApplication({
        name: name.trim(),
        environment,
        protocols,
        ...(protocols.includes("AUTHZEN") ? { pep_client_id: pep.trim() || null, pep_obligations: obligations, pep_context_fields: context } : {}),
      }),
    { invalidate: [APPS_KEY], success: "Application registered", onSuccess: onClose },
  );
  const authzen = protocols.includes("AUTHZEN");
  const valid = name.trim().length > 0 && protocols.length > 0 && (!authzen || pep.trim().length > 0);
  return (
    <Dialog
      isOpen
      onOpenChange={(o) => !o && !create.isPending && onClose()}
      title="Register an application"
      description="An application that signs people in through Zoiko iD or asks it for access decisions."
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
          <TextField label="Name" isRequired value={name} onChange={setName} placeholder="Payroll" />
          <Select label="Environment" value={environment} onChange={(v) => setEnvironment(v as AppEnvironment)} options={ENVIRONMENTS} />
        </div>
        <fieldset className="space-y-2">
          <legend className="text-sm font-medium text-ink">Protocols</legend>
          <div className="grid gap-2 sm:grid-cols-2">
            {PROTOCOLS.map((p) => (
              <Checkbox key={p.id} isSelected={protocols.includes(p.id)} onChange={() => setProtocols((l) => toggle(l, p.id))}>
                {p.label}
              </Checkbox>
            ))}
          </div>
        </fieldset>
        {authzen ? (
          <div className="space-y-4 rounded-card border border-border p-4">
            {mine.length ? (
              <Select
                label="Enforcement point client"
                isRequired
                value={pep || null}
                onChange={setPep}
                options={mine.map((c) => ({ id: c.client_id, label: c.client_id, description: c.client_type === "PUBLIC" ? "Browser or mobile" : "Server" }))}
                description="The OAuth client your application uses when it asks for access decisions. Create one in OAuth clients if the list is empty."
              />
            ) : (
              <TextField
                label="Enforcement point client ID"
                isRequired
                mono
                value={pep}
                onChange={setPep}
                description="No OAuth client yet: close this and create one under OAuth clients, then pick it here."
              />
            )}
            <fieldset className="space-y-2">
              <legend className="text-sm font-medium text-ink">Obligations it enforces</legend>
              <div className="grid gap-2 sm:grid-cols-2">
                {OBLIGATIONS.map((o) => (
                  <Checkbox key={o.id} isSelected={obligations.includes(o.id)} onChange={() => setObligations((l) => toggle(l, o.id))}>
                    {o.label}
                  </Checkbox>
                ))}
              </div>
            </fieldset>
            <fieldset className="space-y-2">
              <legend className="text-sm font-medium text-ink">Context it sends with high-risk requests</legend>
              <div className="grid gap-2 sm:grid-cols-3">
                {CONTEXT.map((c) => (
                  <Checkbox key={c.id} isSelected={context.includes(c.id)} onChange={() => setContext((l) => toggle(l, c.id))}>
                    {c.label}
                  </Checkbox>
                ))}
              </div>
            </fieldset>
          </div>
        ) : null}
        {create.error ? <ApiErrorView error={create.error} title="The application was not registered" /> : null}
      </div>
    </Dialog>
  );
}

/** Application & integration registry (OVERLAY-001 §14): what is connected, and how far along. */
export function ApplicationsPage() {
  const apps = useCellQuery(APPS_KEY, (api) => api.integration.applications());
  const [adding, setAdding] = useState(false);
  const move = useCellMutation((api, v: { id: string; to: AppStatus }) => api.integration.moveApplication(v.id, v.to), {
    invalidate: [APPS_KEY, GATES_KEY],
    success: (a) => `${a.name}: ${a.status.toLowerCase()}`,
  });

  const columns: Column<IntegrationApplication>[] = [
    { key: "name", header: "Application", pinned: true, render: (a) => <span className="font-medium text-ink">{a.name}</span>, sortValue: (a) => a.name },
    { key: "env", header: "Environment", render: (a) => ENVIRONMENTS.find((e) => e.id === a.environment)?.label ?? a.environment },
    { key: "protocols", header: "Protocols", render: (a) => a.protocols.join(", ") },
    { key: "pep", header: "Enforcement point", render: (a) => (a.pep_client_id ? <span className="font-mono text-xs">{a.pep_client_id}</span> : "—") },
    { key: "status", header: "Status", render: (a) => <StatusBadge state={a.status} kind="Application" size="xs" />, sortValue: (a) => a.status },
    {
      key: "actions",
      header: "",
      align: "end",
      render: (a) => (
        <div className="flex justify-end gap-2">
          {(NEXT[a.status] ?? []).map((n) => (
            <Button
              key={n.to}
              size="sm"
              variant={n.to === "ACTIVE" ? "primary" : "secondary"}
              pending={move.isPending && move.variables?.id === a.application_id && move.variables.to === n.to}
              onPress={() => move.mutate({ id: a.application_id, to: n.to })}
            >
              {n.label}
            </Button>
          ))}
        </div>
      ),
    },
  ];

  return (
    <div className="space-y-6">
      <PageHeader
        icon={AppWindow}
        title="Applications"
        description="Applications connected to Zoiko iD. An active application with an enforcement point is needed before shadow evaluation."
        breadcrumbs={[{ label: "Onboarding", href: "/admin/onboarding" }, { label: "Applications" }]}
        actions={
          <Button icon={<Plus aria-hidden className="size-4" />} onPress={() => setAdding(true)}>
            Register application
          </Button>
        }
      />
      <OAuthClientsCard />
      {move.error ? <ApiErrorView error={move.error} title="The application was not moved" /> : null}
      <Card padded={false}>
        <QueryView query={apps}>
          {(r) =>
            r.items.length ? (
              <DataTable caption="Applications" columns={columns} rows={r.items} rowKey={(a) => a.application_id} />
            ) : (
              <EmptyState
                title="No applications yet"
                icon={AppWindow}
                action={
                  <Button icon={<Plus aria-hidden className="size-4" />} onPress={() => setAdding(true)}>
                    Register application
                  </Button>
                }
              >
                Register the first application whose sign-ins or access decisions Zoiko iD will handle.
              </EmptyState>
            )
          }
        </QueryView>
      </Card>
      {adding ? <RegisterApp onClose={() => setAdding(false)} /> : null}
    </div>
  );
}
