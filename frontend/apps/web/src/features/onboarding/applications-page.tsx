"use client";

import type { AppEnvironment, AppStatus, IntegrationApplication } from "@zoiko/api-client";
import { Banner, Button, Card, DataTable, EmptyState, PageHeader, StatusBadge, type Column } from "@zoiko/ui";
import { AppWindow, Plus } from "lucide-react";
import { useState } from "react";

import { ApiErrorView, QueryView, useCellMutation, useCellQuery } from "@/lib/cell";

import { OAuthClientsCard } from "./oauth-clients";
import { RegisterApp } from "./register-app";
import { GATES_KEY } from "./shared";

export const APPS_KEY = ["integration", "applications"];

const ENVIRONMENTS: { id: AppEnvironment; label: string }[] = [
  { id: "PROD", label: "Production" },
  { id: "STAGE", label: "Staging" },
  { id: "TEST", label: "Test" },
  { id: "DEV", label: "Development" },
];
/** The next lifecycle moves offered for an application (integration domain APP_TRANSITIONS). */
const NEXT: Partial<Record<AppStatus, { to: AppStatus; label: string }[]>> = {
  DRAFT: [{ to: "TESTING", label: "Start testing" }],
  TESTING: [{ to: "ACTIVE", label: "Activate" }, { to: "DRAFT", label: "Back to draft" }],
  ACTIVE: [{ to: "SUSPENDED", label: "Suspend" }],
  SUSPENDED: [{ to: "ACTIVE", label: "Resume" }],
};

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
        description="The systems of yours that use Zoiko iD. Add each one, then make it active so onboarding can watch how it behaves."
        breadcrumbs={[{ label: "Onboarding", href: "/admin/onboarding" }, { label: "Applications" }]}
        actions={
          <Button icon={<Plus aria-hidden className="size-4" />} onPress={() => setAdding(true)}>
            Add application
          </Button>
        }
      />
      <Banner tone="info" title="How this works in three steps">
        <ol className="mt-1 list-decimal space-y-0.5 pl-5">
          <li>Add your application. We make it a key so it can prove who it is.</li>
          <li>Press <strong>Start testing</strong>, then <strong>Activate</strong>.</li>
          <li>Back in Onboarding, start shadow. Your application then sends us its access questions.</li>
        </ol>
      </Banner>
      {move.error ? <ApiErrorView error={move.error} title="The application was not moved" /> : null}
      <Card padded={false} title="Your applications">
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
                    Add application
                  </Button>
                }
              >
                Add the first system of yours that Zoiko iD will sign people in to or give access decisions for.
              </EmptyState>
            )
          }
        </QueryView>
      </Card>
      <OAuthClientsCard />
      {adding ? <RegisterApp onClose={() => setAdding(false)} /> : null}
    </div>
  );
}
