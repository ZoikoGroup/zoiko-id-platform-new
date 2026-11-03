"use client";

import type { GovernanceReviewer } from "@zoiko/api-client";
import { Badge, Banner, Button, Card, CopyId, DataTable, DateTime, EmptyState, PageHeader, Select, TextField, type Column } from "@zoiko/ui";
import { UserCheck } from "lucide-react";
import { useState } from "react";

import { ApiErrorView, QueryView, useCellMutation, useCellQuery } from "@/lib/cell";
import { StepUpCancelled } from "@/lib/step-up";

import {
  REVIEWER_IS_NOT,
  RELATIONSHIPS,
  emptyReviewerForm,
  reviewerFormErrors,
  reviewerStatusTone,
  toAppointment,
  type ReviewerForm,
} from "./logic";

const CASES_KEY = ["org-approvals"];
const REVIEWERS_KEY = ["governance", "reviewers"];

/** A refusal from the server. A cancelled passkey prompt is not an error. */
function Refusal({ error, title }: { error: unknown; title: string }) {
  if (error instanceof StepUpCancelled) return null;
  return <ApiErrorView error={error} title={title} />;
}

/** Solo status, the appointed independent reviewers, and the form to appoint one. Held requests are never waived. */
export function GovernancePage() {
  const solo = useCellQuery(["governance", "solo"], (api) => api.approvals.soloStatus());
  const reviewers = useCellQuery(REVIEWERS_KEY, (api) => api.approvals.reviewers());
  const [form, setForm] = useState<ReviewerForm>(emptyReviewerForm);
  const [shown, setShown] = useState(false);
  const errors = reviewerFormErrors(form);
  const appoint = useCellMutation((api) => api.approvals.appointReviewer(toAppointment(form)), {
    invalidate: [REVIEWERS_KEY, CASES_KEY, ["governance", "solo"]],
    success: "Reviewer proposed. Their appointment needs an independent approval.",
    onSuccess: () => {
      setForm(emptyReviewerForm);
      setShown(false);
    },
  });
  const revoke = useCellMutation((api, id: string) => api.approvals.revokeReviewer(id), { invalidate: [REVIEWERS_KEY, ["governance", "solo"]], success: "Appointment ended" });
  const set = (k: keyof ReviewerForm) => (v: string) => setForm((f) => ({ ...f, [k]: v }));
  const err = (k: keyof ReviewerForm) => (shown ? errors[k] : undefined);
  const columns: Column<GovernanceReviewer>[] = [
    { key: "who", header: "Reviewer", render: (r) => <CopyId value={r.principal_id} label="reviewer account" /> },
    { key: "rel", header: "Relationship", render: (r) => RELATIONSHIPS.find((x) => x.id === r.relationship)?.label ?? r.relationship },
    { key: "status", header: "Status", render: (r) => <Badge tone={reviewerStatusTone(r)}>{r.status === "PROPOSED" ? "Proposed, not yet active" : r.status === "ACTIVE" ? "Active" : r.status === "REVOKED" ? "Ended" : "Expired"}</Badge> },
    { key: "via", header: "Verified by", render: (r) => (r.activated_via === "PLATFORM_VERIFICATION" ? "Zoiko iD verification" : r.activated_via === "A1_APPROVAL" ? "Independent approval" : "Not yet"), defaultHidden: true },
    { key: "exp", header: "Valid until", render: (r) => <DateTime value={r.expires_at} /> },
    {
      key: "act",
      header: "",
      render: (r) =>
        r.status === "PROPOSED" || r.status === "ACTIVE" ? (
          <Button size="sm" variant="secondary" pending={revoke.isPending} onPress={() => revoke.mutate(r.reviewer_id)}>End appointment</Button>
        ) : null,
    },
  ];
  return (
    <div className="space-y-6">
      <PageHeader
        icon={UserCheck}
        title="Governance and independent reviewers"
        description="Who can independently approve your organization's sensitive and critical changes."
        breadcrumbs={[{ label: "Approvals", href: "/organizations/current/approvals" }, { label: "Governance" }]}
      />
      <QueryView query={solo}>
        {(s) => (
          <Banner tone={s.solo_mode ? "info" : "ok"} title={s.solo_mode ? "Solo organization" : `${s.eligible_human_member_count} people can approve`}>
            {s.message ?? `Critical changes need ${s.a2_requires}.`} Being a solo organization never lowers this: a critical change still needs two other people, and it waits until they exist.
            {s.independent_routes.length ? " You have an appointed independent reviewer." : ""}
            {!s.legal_form_resolved ? " Your legal form and who may represent the company are decided by the company verification, not by the number of members." : ""}
          </Banner>
        )}
      </QueryView>
      <Card title="Appointed reviewers">
        <p className="mb-3 text-sm text-ink-muted">{REVIEWER_IS_NOT}</p>
        <QueryView query={reviewers}>
          {(d) =>
            d.items.length === 0 ? (
              <EmptyState title="No reviewers appointed">Appoint a verified outside person so critical changes can be approved without hiring.</EmptyState>
            ) : (
              <DataTable caption="Independent reviewers" columns={columns} rows={d.items} rowKey={(r) => r.reviewer_id} />
            )
          }
        </QueryView>
        <p className="mt-3 text-xs text-ink-subtle">
          An appointment is only a proposal until an independent person approves it, or Zoiko iD verifies the reviewer&apos;s identity and authority. You cannot appoint yourself. Zoiko iD Assured Review is not available.
        </p>
      </Card>
      <Card title="Appoint a reviewer">
        <form
          className="grid gap-3 sm:grid-cols-2"
          onSubmit={(e) => {
            e.preventDefault();
            setShown(true);
            if (Object.keys(errors).length === 0) appoint.mutate(undefined);
          }}
        >
          <TextField label="Reviewer's account id" value={form.principal} onChange={set("principal")} errorMessage={err("principal")} mono isRequired description="They need an account in this organization and a passkey." />
          <Select label="How they relate to you" value={form.relationship} onChange={set("relationship")} options={RELATIONSHIPS} errorMessage={err("relationship")} isRequired />
          <TextField label="Documented authority" value={form.basis} onChange={set("basis")} errorMessage={err("basis")} multiline rows={3} isRequired description="For example a licence, a trust deed or a directorship on the register." />
          <TextField label="Independence declaration" value={form.declaration} onChange={set("declaration")} errorMessage={err("declaration")} multiline rows={3} isRequired description="State that they have no tie that would stop them judging independently." />
          <TextField className="sm:col-span-2" label="People they have a relationship with (optional)" value={form.conflicts} onChange={set("conflicts")} errorMessage={err("conflicts")} multiline rows={2} mono description="Account ids, one per line. They cannot approve those people's requests." />
          <div className="sm:col-span-2">
            <Button type="submit" pending={appoint.isPending}>Propose this reviewer</Button>
          </div>
          {appoint.error ? <div className="sm:col-span-2"><Refusal error={appoint.error} title="The reviewer was not proposed" /></div> : null}
        </form>
      </Card>
      {revoke.error ? <Refusal error={revoke.error} title="The appointment was not ended" /> : null}
    </div>
  );
}
