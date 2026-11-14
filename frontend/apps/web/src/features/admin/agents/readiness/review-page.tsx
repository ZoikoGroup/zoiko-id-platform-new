"use client";

import type { ReadinessProposal } from "@zoiko/api-client";
import { PageHeader } from "@zoiko/ui";
import { useState } from "react";

import { useCellMutation, useCellQuery } from "@/lib/cell";

import { PersonSelect } from "../pickers";
import { READINESS_KEY } from "./logic";
import { AccessReviewProposalCard } from "./proposal-card";
import { ReadinessQuery, WriteError } from "./shared";

/** WF-AG-04. A person reviews what an agent suggested. Every button records the person's decision; none changes access. */
export function ReviewProposalPage({ proposalId }: { proposalId: string }) {
  const key = [...READINESS_KEY, "proposal", proposalId];
  const proposal = useCellQuery(key, (api) => api.accessReadiness.proposal(proposalId));
  const [assignee, setAssignee] = useState("");
  const [failure, setFailure] = useState<unknown>(null);
  const opts = { invalidate: [READINESS_KEY] };
  const dismiss = useCellMutation((api) => api.accessReadiness.dismissProposal(proposalId), { ...opts, success: "Finding dismissed" });
  const assign = useCellMutation((api, who: string) => api.accessReadiness.assignProposal(proposalId, who), { ...opts, success: "Assigned for investigation" });
  const handoff = useCellMutation((api) => api.accessReadiness.handoffProposal(proposalId), { ...opts, success: "Handoff recorded. Nothing was changed." });
  const pending = dismiss.isPending || assign.isPending || handoff.isPending;

  async function run(fn: () => Promise<ReadinessProposal>) {
    setFailure(null);
    try {
      await fn();
      await proposal.refetch();
    } catch (e) {
      setFailure(e);
    }
  }

  return (
    <>
      <PageHeader
        title="Review suggested access change"
        description="An agent suggested this. It is a review queue item, not an access change."
        breadcrumbs={[{ label: "Agents & tools", href: "/admin/agents" }, { label: "Access readiness", href: "/admin/agents/readiness" }, { label: "Review" }]}
      />
      <ReadinessQuery query={proposal}>
        {(p) => (
          <div className="space-y-4">
            <AccessReviewProposalCard
              proposal={p}
              pending={pending}
              canAssign={Boolean(assignee)}
              assigneeField={<PersonSelect label="Assign to" value={assignee} onChange={setAssignee} description="An active member of this workspace." />}
              onDismiss={() => void run(() => dismiss.mutateAsync(undefined))}
              onAssign={() => void run(() => assign.mutateAsync(assignee))}
              onHandoff={() => void run(() => handoff.mutateAsync(undefined))}
            />
            {failure ? <WriteError error={failure} /> : null}
          </div>
        )}
      </ReadinessQuery>
    </>
  );
}
