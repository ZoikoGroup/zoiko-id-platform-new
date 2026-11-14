"use client";

import type { ReadinessProposal, ReadinessProposalStatus } from "@zoiko/api-client";
import { Badge, Banner, Button, Card, KeyValues } from "@zoiko/ui";
import type { ReactNode } from "react";

import { RULE_TEXT, severityTone } from "./logic";

export const NO_CHANGE_NOTICE = "No access permissions will change as a result of this review proposal.";

const CAN: Record<ReadinessProposalStatus, { dismiss: boolean; assign: boolean; handoff: boolean }> = {
  OPEN: { dismiss: true, assign: true, handoff: true },
  ASSIGNED: { dismiss: true, assign: false, handoff: true },
  DISMISSED: { dismiss: false, assign: false, handoff: false },
  HANDED_OFF: { dismiss: false, assign: false, handoff: false },
};

/** WF-AG-04. A review queue item, not an administration shortcut: it records a person's decision and offers no way to execute a change. */
export function AccessReviewProposalCard({
  proposal,
  assigneeField,
  canAssign,
  pending,
  onDismiss,
  onAssign,
  onHandoff,
}: {
  proposal: ReadinessProposal;
  /** The control that picks who to assign it to. */
  assigneeField: ReactNode;
  canAssign: boolean;
  pending?: boolean;
  onDismiss: () => void;
  onAssign: () => void;
  onHandoff: () => void;
}) {
  const can = CAN[proposal.status];
  return (
    <div className="space-y-4">
      <Banner tone="info" title="No access permissions will change">
        {NO_CHANGE_NOTICE}
      </Banner>
      <Card title="Review suggested access change" description={`Suggestion ${proposal.proposal_id}`}>
        <div className="space-y-5">
          <p className="text-sm text-ink-muted">
            Submitted by <strong className="text-ink">an agent, not a person</strong> ({proposal.submitted_by.agent_id}).
          </p>
          <KeyValues
            columns={2}
            items={[
              ["Affected mandate (masked)", <code key="t" className="font-mono text-xs">{proposal.target}</code>],
              ["Rule finding", `${proposal.rule_id} ${RULE_TEXT[proposal.rule_id] ?? ""} · ${proposal.finding_code}`.trim()],
              ["Rule version", proposal.rule_version],
              ["Status", <Badge key="s">{proposal.status.replace("_", " ").toLowerCase()}</Badge>],
            ]}
          />
          <section aria-label="Severity and confidence" className="grid gap-3 sm:grid-cols-2">
            <div className="rounded-card border border-border p-3 text-sm">
              <h3 className="text-xs font-semibold uppercase tracking-wider text-ink-subtle">Rule severity</h3>
              <p className="mt-1">
                <Badge tone={severityTone(proposal.severity)}>{proposal.severity}</Badge>
              </p>
              <p className="mt-1 text-xs text-ink-muted">How serious the finding is if it is right.</p>
            </div>
            <div className="rounded-card border border-border p-3 text-sm">
              <h3 className="text-xs font-semibold uppercase tracking-wider text-ink-subtle">Confidence and basis</h3>
              <p className="mt-1 font-medium">{proposal.confidence === "DETERMINISTIC" ? "Deterministic rule" : proposal.confidence}</p>
              <p className="mt-1 text-xs text-ink-muted">How the finding was reached: a fixed rule applied to the record, not an estimate.</p>
            </div>
          </section>
          <div>
            <h3 className="text-xs font-semibold uppercase tracking-wider text-ink-subtle">Evidence</h3>
            {proposal.evidence_refs.length ? (
              <ul className="mt-1 list-disc ps-5 text-sm">
                {proposal.evidence_refs.map((r) => (
                  <li key={r} className="break-all font-mono text-xs">{r}</li>
                ))}
              </ul>
            ) : (
              <p className="mt-1 text-sm text-warn-fg">No evidence reference is attached to this proposal.</p>
            )}
          </div>
          {proposal.handoff ? (
            <Banner tone="ok" title="Handoff recorded">
              A signed request to open the governed access workflow was recorded {proposal.handoff.signed_at}. It executes nothing. The access change service checks its own
              permissions and policy, and a person carries out any change there.
              <span className="mt-1 block break-all font-mono text-xs">{proposal.handoff.handoff_digest}</span>
            </Banner>
          ) : null}
          {can.assign ? <div className="max-w-md">{assigneeField}</div> : null}
        </div>
        <div className="mt-6 flex flex-col gap-2 border-t border-border pt-4 sm:flex-row sm:flex-wrap">
          <Button variant="secondary" size="lg" className="w-full sm:w-auto" isDisabled={!can.dismiss || pending} onPress={onDismiss}>
            Dismiss finding
          </Button>
          <Button variant="secondary" size="lg" className="w-full sm:w-auto" isDisabled={!can.assign || !canAssign || pending} onPress={onAssign}>
            Assign for investigation
          </Button>
          <Button size="lg" className="w-full sm:w-auto" isDisabled={!can.handoff || pending} onPress={onHandoff}>
            Open governed access workflow
          </Button>
        </div>
        <p className="mt-2 text-xs text-ink-subtle">
          Opening the governed access workflow records a signed handoff only. It changes nothing by itself.
        </p>
      </Card>
    </div>
  );
}
