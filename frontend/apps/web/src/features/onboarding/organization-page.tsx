"use client";

import type { TenantView } from "@zoiko/api-client";
import { Banner, ButtonLink, Card, CopyId, KeyValues, PageHeader, StatusBadge } from "@zoiko/ui";
import { Building2, CheckCircle2, ScanFace, Search } from "lucide-react";

import { QueryView, useCellQuery } from "@/lib/cell";

import { type CarriedOrganization, DevelopmentNotice, GATES_KEY, GateList, standing, useTenant } from "./shared";

function Verified({ tenant, development }: { tenant: TenantView; development: boolean }) {
  const gates = useCellQuery(GATES_KEY, (api) => api.onboarding.gates());
  return (
    <div className="space-y-4">
      {development ? <DevelopmentNotice /> : null}
      <Banner tone="ok" icon={CheckCircle2} title="Company verified">
        The company is established from its official register and you are its owner. Continue setting it up from{" "}
        <a href="/admin/onboarding" className="font-medium text-brand-700 hover:underline">
          Onboarding
        </a>
        .
      </Banner>
      <Card title="Organization">
        <KeyValues
          columns={2}
          items={[
            ["Company", tenant.display_name],
            ["Workspace", <span key="w" className="font-mono">{tenant.tenant_slug}</span>],
            ["Organization", tenant.organization_id ? <CopyId key="o" value={tenant.organization_id} label="organization" /> : "—"],
            ["Status", <StatusBadge key="s" state={tenant.status} kind="Tenant" />],
          ]}
        />
      </Card>
      <QueryView query={gates}>{(g) => <GateList gates={g.gates} empty="Every gate of this stage has passed." />}</QueryView>
    </div>
  );
}

/** Company verification (ZID-UX-WF-013B; OVERLAY-001 §4). There is one way in, for every country: find the organization in its official register and make a
 * claim. The claim is provisional; it becomes the organization only when the entity, the applicant's identity, the authority and the screening are each
 * complete. Nothing here grants ownership from a registry match or a typed name. */
export function OrganizationPage({ carry }: { carry: CarriedOrganization }) {
  const tenant = useTenant();
  return (
    <div className="space-y-6">
      <PageHeader
        icon={Building2}
        title="Company verification"
        description="Find your organization in its official register, then show that you can act for it. You become its owner only when every check is complete."
        breadcrumbs={[{ label: "Overview", href: "/overview" }, { label: "Company verification" }]}
      />
      <QueryView query={tenant}>
        {(t) => {
          if (!t) return <Banner tone="warn" title="No workspace">You are not part of a workspace.</Banner>;
          const me = standing(t);
          const verifying = t.status === "DRAFT" || t.status === "ORGANIZATION_VERIFYING";
          if (!verifying) return <Verified tenant={t} development={!!carry.development} />;
          if (!me.pendingOwner) {
            return (
              <Banner tone="info" title="Verification in progress">
                The person who started the organization is verifying it. You can continue once it is verified.
              </Banner>
            );
          }
          return (
            <div className="grid gap-6 lg:grid-cols-2">
              <Card title="1. Verify your identity" description="Your verified legal name is compared with the people the register lists for the company.">
                <div className="space-y-3">
                  <p className="text-sm text-ink-muted">
                    Complete identity verification once. Only a commitment to your verified name is compared with the register; your documents are not shared
                    with the company.
                  </p>
                  <ButtonLink href="/account/verification" variant="secondary" icon={<ScanFace aria-hidden className="size-4" />}>
                    Verify my identity
                  </ButtonLink>
                </div>
              </Card>
              <Card title="2. Find your organization" description="Choose the country the company is incorporated in, search its official register and pick the right entry.">
                <div className="space-y-3">
                  <p className="text-sm text-ink-muted">
                    Then say how you act for it and add any evidence asked for. Each check is shown on its own, and nothing is verified by searching.
                  </p>
                  <ButtonLink href="/admin/organization/claim" icon={<Search aria-hidden className="size-4" />}>
                    Find my organization
                  </ButtonLink>
                </div>
              </Card>
            </div>
          );
        }}
      </QueryView>
    </div>
  );
}
