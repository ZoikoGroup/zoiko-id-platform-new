/** The presentational pieces of the WF-013C screens as a person meets them: words first, a reason and a next step, the shortage link,
 * and nothing about evidence or payload on an inbox card. */
import type { OnboardingItem, OrgApprovalSummary } from "@zoiko/api-client";
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { ChecklistItem, RequestCard, StatusWord } from "./parts";

afterEach(cleanup);

const item = (o: Partial<OnboardingItem>): OnboardingItem => ({
  key: "authority",
  title: "Authority to represent the company",
  status: "Required",
  required: true,
  done: false,
  reason: "This check has not been completed. A register match or a payment does not complete it.",
  next_action: "Continue company verification",
  ...o,
});
const card = (o: Partial<OrgApprovalSummary> = {}): OrgApprovalSummary => ({
  request_id: "01a1-xyz",
  title: "ownership-transfer-or-replacement on principal:abc",
  requester_principal_id: "p-req",
  level: "A2",
  state: "PENDING_APPROVAL",
  status_label: "Pending approval",
  approvals_received: 0,
  approvals_required: 2,
  shortage: null,
  policy_version: "pol",
  expires_at: "2026-11-05T00:00:00Z",
  created_at: "2026-11-02T00:00:00Z",
  next_action: "Review and decide",
  ...o,
});

describe("a checklist item", () => {
  it("shows the status as a word with its reason and a link to the real next step", () => {
    render(<ul><ChecklistItem item={item({})} /></ul>);
    expect(screen.getByText("Required")).toBeTruthy();
    expect(screen.getByText(/does not complete it/)).toBeTruthy();
    const link = screen.getByRole("link", { name: "Continue company verification" });
    expect(link.getAttribute("href")).toBe("/admin/organization");
  });

  it("shows no next step for a finished item and marks a recommended one as recommended", () => {
    render(<ul><ChecklistItem item={item({ status: "Verified", done: true, reason: null, next_action: null })} /></ul>);
    expect(screen.getByText("Verified")).toBeTruthy();
    expect(screen.queryByRole("link")).toBeNull();
    cleanup();
    render(<ul><ChecklistItem item={item({ key: "approvers", title: "Independent approvers", required: false, next_action: "Invite approvers, or appoint verified independent reviewers" })} /></ul>);
    expect(screen.getByText("Recommended")).toBeTruthy();
    expect(screen.getByRole("link").getAttribute("href")).toBe("/admin/approvals/governance");
  });

  it("offers the acceptance control passed to it for an item that needs one", () => {
    render(<ul><ChecklistItem item={item({ key: "controls", title: "Accept the organizational controls", next_action: "Read and accept the controls" })}><button type="button">Accept</button></ChecklistItem></ul>);
    expect(screen.getByRole("button", { name: "Accept" })).toBeTruthy();
    expect(screen.queryByRole("link")).toBeNull(); // acceptance is on the page, not a link away
  });
});

describe("an inbox card", () => {
  it("names the request, the level, the quorum and the next action, with a link to the decision page", () => {
    render(<ul><RequestCard orgId="t1" r={card()} /></ul>);
    const link = screen.getByRole("link", { name: /ownership-transfer-or-replacement/ });
    expect(link.getAttribute("href")).toBe("/organizations/t1/approvals/01a1-xyz");
    expect(screen.getByText("Pending approval")).toBeTruthy();
    expect(screen.getByText("Critical (A2)")).toBeTruthy();
    expect(screen.getByText("0 of 2")).toBeTruthy();
    expect(screen.getByText("Review and decide")).toBeTruthy();
    expect(screen.queryByText(/payload|evidence/i)).toBeNull(); // the card never carries the change or its evidence
  });

  it("says 'Independent approvers required' and links to remediation when the server reports a shortage", () => {
    render(<ul><RequestCard orgId="t1" r={card({ shortage: "Independent approvers required", next_action: "Invite or appoint independent approvers" })} /></ul>);
    const li = screen.getByRole("listitem");
    expect(within(li).getByText(/Independent approvers required\./)).toBeTruthy();
    expect(within(li).getByRole("link", { name: "Invite or appoint independent approvers" }).getAttribute("href")).toBe("/admin/approvals/governance");
    expect(within(li).getByText(/never waived or simulated/)).toBeTruthy();
  });

  it("uses the sensitive label for a one-approver request", () => {
    render(<ul><RequestCard orgId="t1" r={card({ level: "A1", approvals_required: 1 })} /></ul>);
    expect(screen.getByText("Sensitive (A1)")).toBeTruthy();
    expect(screen.getByText("0 of 1")).toBeTruthy();
  });
});

describe("a status word", () => {
  it("is always text", () => {
    render(<StatusWord word="Needs changes" tone="warn" />);
    expect(screen.getByText("Needs changes")).toBeTruthy();
  });
});
