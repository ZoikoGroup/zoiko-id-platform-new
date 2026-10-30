"use client";

import type { OrganizationResult, TenantView } from "@zoiko/api-client";
import { Banner, Button, ButtonLink, Card, CopyId, DateTime, KeyValues, PageHeader, Select, StatusBadge, TextField } from "@zoiko/ui";
import { Building2, CheckCircle2, ScanFace } from "lucide-react";
import { useState } from "react";

import { ApiErrorView, QueryView, useCellMutation, useCellQuery } from "@/lib/cell";

import {
  type CarriedOrganization,
  DevelopmentNotice,
  GATES_KEY,
  GateList,
  TENANT_KEY,
  companyNumberValid,
  leiValid,
  normalizeCompanyNumber,
  standing,
  useTenant,
} from "./shared";

const REGISTERS = [{ id: "GB-COH", label: "United Kingdom — Companies House" }];

/** Why the register check did not pass, in words (ORG-001 §23 codes, as the gate reports them). */
const OUTCOME_HELP: [RegExp, string][] = [
  [/NAME|HOLDER|OFFICER|MATCH/i, "Your verified name does not match an active officer of this company. Complete your identity verification first, or ask an officer to register the company."],
  [/DISSOLVED|INACTIVE|STATUS/i, "The register shows the company is not active, so it cannot be verified."],
  [/NOT_FOUND|UNKNOWN/i, "The register has no company with this number. Check the number and try again."],
  [/CLAIM|ASSURANCE|VERIF/i, "Your identity has not been verified yet. Complete identity verification, then submit again."],
];

function explain(detail: string | undefined): string | null {
  if (!detail) return null;
  return OUTCOME_HELP.find(([re]) => re.test(detail))?.[1] ?? null;
}

/** Prefilled from what the company declared at sign-up (stored with the tenant); the link the
 * registration flow carries is only the fallback for a workspace created before that was stored. */
function DeclaredVerifyForm({ initial, onDone }: { initial: CarriedOrganization; onDone: (r: OrganizationResult) => void }) {
  const declared = useCellQuery(["onboarding", "declared"], (api) => api.onboarding.declaredOrganization());
  if (declared.isPending) return <p className="text-sm text-ink-muted">Loading what you declared at registration…</p>;
  const d = declared.data?.declared;
  return (
    <VerifyForm
      key={d?.identifier ?? "none"}
      initial={d ? { companyNumber: d.identifier, lei: d.lei ?? undefined } : initial}
      declaredAt={d?.declared_at}
      onDone={onDone}
    />
  );
}

function VerifyForm({
  initial,
  declaredAt,
  onDone,
}: {
  initial: CarriedOrganization;
  declaredAt?: string;
  onDone: (r: OrganizationResult) => void;
}) {
  const [number, setNumber] = useState(initial.companyNumber ?? "");
  const [lei, setLei] = useState(initial.lei ?? "");
  const submit = useCellMutation(
    (api, v: { identifier: string; lei: string }) =>
      api.onboarding.submitOrganization({ namespace: "GB-COH", identifier: v.identifier, lei: v.lei || null }),
    { invalidate: [TENANT_KEY, GATES_KEY], onSuccess: (r) => onDone(r) },
  );
  const ok = companyNumberValid(number) && (!lei.trim() || leiValid(lei));
  return (
    <form
      className="space-y-4"
      onSubmit={(e) => {
        e.preventDefault();
        if (ok) submit.mutate({ identifier: normalizeCompanyNumber(number), lei: lei.trim().toUpperCase() });
      }}
    >
      {declaredAt ? (
        <p className="text-xs text-ink-muted">
          From your registration on <DateTime value={declaredAt} />. Correct it here if it was mistyped.
        </p>
      ) : null}
      <Select label="Register" value="GB-COH" onChange={() => undefined} options={REGISTERS} isRequired />
      <TextField
        label="Company number"
        isRequired
        mono
        value={number}
        onChange={(v) => setNumber(v.toUpperCase())}
        description="The 8-character Companies House number."
        errorMessage={number && !companyNumberValid(number) ? "Enter 8 digits, or two letters and 6 digits." : undefined}
      />
      <TextField
        label="LEI (optional)"
        mono
        value={lei}
        onChange={(v) => setLei(v.toUpperCase())}
        errorMessage={lei && !leiValid(lei) ? "An LEI is 20 characters." : undefined}
      />
      <Button type="submit" pending={submit.isPending} isDisabled={!ok} icon={<Building2 aria-hidden className="size-4" />}>
        Verify company
      </Button>
      {submit.error ? <ApiErrorView error={submit.error} title="The company could not be checked" /> : null}
    </form>
  );
}

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

/** Company verification (OVERLAY-001 §4; ORG-001 §19): the company is resolved from its official
 * register and the registrant is matched to one of its officers before becoming OWNER. */
export function OrganizationPage({ carry }: { carry: CarriedOrganization }) {
  const tenant = useTenant();
  const [result, setResult] = useState<OrganizationResult | null>(null);

  return (
    <div className="space-y-6">
      <PageHeader
        icon={Building2}
        title="Company verification"
        description="Your company is checked against its official register, and you are matched to one of its officers, before you become its owner."
        breadcrumbs={[{ label: "Overview", href: "/overview" }, { label: "Company verification" }]}
      />
      <Banner
        tone="info"
        title="Is your company outside the United Kingdom?"
        action={
          <ButtonLink href="/admin/organization/claim" size="sm" variant="secondary">
            Find your organization
          </ButtonLink>
        }
      >
        Search an official register in any country where company verification is open, and track each check on its own.
      </Banner>
      <QueryView query={tenant}>
        {(t) => {
          if (!t) return <Banner tone="warn" title="No company workspace">You are not part of a company workspace.</Banner>;
          const me = standing(t);
          const verifying = t.status === "DRAFT" || t.status === "ORGANIZATION_VERIFYING";
          if (!verifying) return <Verified tenant={t} development={!!carry.development} />;
          if (!me.pendingOwner) {
            return (
              <Banner tone="info" title="Verification in progress">
                The person who registered {t.display_name} is verifying the company. You can continue once it is verified.
              </Banner>
            );
          }
          const failed = result?.gates.find((g) => g.status !== "PASS");
          return (
            <div className="grid gap-6 lg:grid-cols-2">
              <Card title="1. Verify your identity" description="The register lists the company's officers. Your verified legal name must match one of them.">
                <div className="space-y-3">
                  <p className="text-sm text-ink-muted">
                    Complete identity verification once. Only a commitment to your verified name is compared with the register; your documents are
                    not shared with the company.
                  </p>
                  <ButtonLink href="/account/verification" variant="secondary" icon={<ScanFace aria-hidden className="size-4" />}>
                    Verify my identity
                  </ButtonLink>
                </div>
              </Card>
              <Card title="2. Verify the company" description="We look the company up in Companies House and confirm it is active.">
                <DeclaredVerifyForm initial={carry} onDone={setResult} />
              </Card>
              {result ? (
                <div className="lg:col-span-2 space-y-3">
                  {failed ? (
                    <Banner tone="warn" title="Not verified yet">
                      {explain(failed.detail) ?? failed.detail ?? "A verification gate did not pass."}
                    </Banner>
                  ) : (
                    <Banner tone="ok" icon={CheckCircle2} title={`${result.legal_name ?? "Company"} verified`}>
                      You are now the company&apos;s owner.{" "}
                      {result.root_fresh_until ? (
                        <>
                          The register record is re-checked before <DateTime value={result.root_fresh_until} />.
                        </>
                      ) : null}
                    </Banner>
                  )}
                  <GateList gates={result.gates} />
                </div>
              ) : null}
            </div>
          );
        }}
      </QueryView>
    </div>
  );
}
