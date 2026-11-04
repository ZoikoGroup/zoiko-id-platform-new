import type { ApprovalCase } from "@zoiko/api-client";

/**
 * What the approval screens say (ZID-ENG-DEC-010 section 08, ZID-ENG-DEC-012 section 05). The server decides who may vote and states
 * why not; this only turns its codes into the plain words the records ask for. It never decides anything.
 */

const REFUSALS: Record<string, string> = {
  VOTER_IS_REQUESTER: "You submitted or changed this request, so someone independent has to approve it.",
  VOTER_IS_EDITOR: "You submitted or changed this request, so someone independent has to approve it.",
  VOTER_IS_BENEFICIARY: "This change is for you, so someone independent has to approve it.",
  VOTER_ALREADY_VOTED: "You have already voted on this request. One person counts once, however many accounts they have.",
  VOTER_NO_AUTHORITY: "Independent approver required. You do not hold an approver role for this request.",
  VOTER_AUTHORITY_MANUFACTURED: "Independent approver required. Your approver role was granted by the person who made this request.",
  VOTER_NOT_HUMAN: "Only a person can approve.",
  VOTER_REVIEWER_CONFLICT: "You declared a relationship with someone involved in this request, so you cannot approve it.",
  APPROVAL_EXPIRED: "This request expired. Nothing changed. Start a new request.",
  APPROVAL_INVALIDATED: "This request can no longer be approved because it, or the policy or region it was made under, changed. Start a new request.",
  APPROVAL_QUORUM_LOST: "An approver no longer holds authority, so the approvals no longer count. Nothing changed.",
  APPROVAL_HASH_MISMATCH: "This request changed since you opened it. Reload and review it again.",
  APPROVAL_STATE_INVALID: "This request is no longer open for a vote.",
  APPROVAL_CASE_REQUIRED: "This is a critical change. Request it for approval by two independent people.",
  STEP_UP_REQUIRED: "Confirm with your passkey for this exact request.",
  ORGANIZATION_NOT_READY: "Your organization is not in a state to make this change right now. Open Organization setup to see what needs attention.",
  APPROVAL_PAYLOAD_INVALID: "The details do not fit this request. Check them and try again.",
  APPROVAL_WOULD_ORPHAN_TENANT: "The last Owner cannot be removed. Add the successor first.",
  INCOMING_OWNER_STRONG_AUTHENTICATOR: "The new Owner needs a passkey before ownership can move to them.",
  OWNER_REMAINS: "Another Owner must remain after this change.",
  REMAINING_OWNER_STRONG_AUTHENTICATOR: "An Owner who stays needs a passkey, so someone can still confirm sensitive actions.",
  RECOVERY_TARGET_ACTIVE_PERSON: "That account is not an active person in this organization.",
  RECOVERY_TARGET_PRIVILEGED: "That account holds no privileged role. Use ordinary recovery instead.",
  APPROVAL_REQUESTER_NOT_PERMITTED: "You do not have the right to ask for this change.",
};

export function approvalRefusalText(code: string | undefined | null): string {
  return (code && REFUSALS[code]) || "This could not be done.";
}

/** The text of the Approve button: "Approve request" for A1, "Approve — 1 of 2" or "Approve — 2 of 2" for A2. */
export function approveLabel(c: Pick<ApprovalCase, "tier" | "approvals_received" | "approvals_required">): string {
  if (c.tier === "A1") return "Approve request";
  const next = Math.min(c.approvals_received + 1, c.approvals_required);
  return `Approve — ${next} of ${c.approvals_required}`;
}

/** The standing notice under a case: a first A2 vote changes nothing, and a held case says what is missing. */
export function caseNotice(c: Pick<ApprovalCase, "state" | "hold" | "approvals_received" | "approvals_required">): string {
  switch (c.state) {
    case "PARTIALLY_APPROVED":
      return `${c.approvals_received} of ${c.approvals_required} independent approvals received. No changes have taken effect.`;
    case "APPROVED_READY":
      return "Approved. No changes have taken effect until it is applied.";
    case "EXECUTED":
      return "Applied.";
    case "FAILED":
      return "The change was not applied. The approved request is unchanged and can be run again until it expires.";
    case "REJECTED":
      return "Rejected. Nothing changed.";
    case "EXPIRED":
      return "Expired. Nothing changed.";
    case "INVALIDATED":
      return "No longer valid. Nothing changed. Start a new request.";
    case "CANCELLED":
      return "Withdrawn. Nothing changed.";
    default:
      return c.hold ? holdText(c.hold) : "Waiting for independent approval. No changes have taken effect.";
  }
}

export function holdText(hold: NonNullable<ApprovalCase["hold"]>): string {
  return hold === "BLOCKED_INSUFFICIENT_APPROVERS"
    ? "Held. This critical change needs two independent approvers and your organization does not have them yet. Appoint verified independent reviewers or add people with an approver role. Nothing has changed, and nothing is waived."
    : "Held. This change needs one independent approver and your organization does not have one yet. Appoint a verified independent reviewer. Nothing has changed.";
}

/** "Approved" and "Applied" are different moments and are shown separately. */
export function approvedVersusApplied(c: Pick<ApprovalCase, "approved_at" | "applied_at">): { approved: string | null; applied: string | null } {
  return { approved: c.approved_at, applied: c.applied_at };
}
