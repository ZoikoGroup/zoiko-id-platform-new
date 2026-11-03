import type { OrgApprovalCase } from "@zoiko/api-client";
import { describe, expect, it } from "vitest";

import {
  ATTACHMENT_KINDS,
  CONFLICT_MESSAGE,
  READINESS_WORDS,
  SOLO_MESSAGE,
  attachmentKey,
  attachmentMessage,
  attachmentProblem,
  attachmentSize,
  blockerLines,
  canApply,
  canDecide,
  canRequestChanges,
  canResubmit,
  countsAnnouncement,
  decisionKey,
  deltaRows,
  hasFilters,
  holdWords,
  inboxQuery,
  itemTone,
  needsReason,
  needsStepUp,
  noFilters,
  refusalKind,
  remediationHref,
  remediationKeyFor,
  requestHref,
  resolveOrgId,
  statusTone,
  stepUpDraft,
} from "./logic";

const base = {
  case_id: "01a1-case",
  request_hash: "a".repeat(64),
  tier: "A2",
  level: "A2",
  version: 2,
  requester_principal_id: "p-req",
  policy_version: "pol#1",
  region: "uk-1",
  expires_at: "2026-11-05T00:00:00Z",
  reason: "Hand over ownership",
  payload: { principal_id: "p-new", role: "OWNER" },
  state: "PENDING_APPROVAL",
  you: { may_vote: true, reasons: [], is_requester: false },
  execution_hold: null,
} as unknown as OrgApprovalCase;
const withCase = (c: Partial<OrgApprovalCase>) => ({ ...base, ...c }) as OrgApprovalCase;

describe("which organization", () => {
  it("resolves 'current' to the caller's own organization and never invents one", () => {
    expect(resolveOrgId("current", "t-1")).toBe("t-1");
    expect(resolveOrgId("current", null)).toBeNull();
    expect(resolveOrgId("t-9", "t-1")).toBe("t-9"); // an explicit id is passed through; the server answers only for the caller's own
    expect(requestHref("t 1", "a/b")).toBe("/organizations/t%201/approvals/a%2Fb");
  });
});

describe("the inbox request", () => {
  it("sends only what the person set, and the cursor only for a further page", () => {
    expect(inboxQuery("review", noFilters)).toEqual({ scope: "review", limit: 25 });
    const q = inboxQuery("mine", { risk: "A2", state: "EXECUTED", from: "2026-11-01", q: "  01A1  " }, "cur|1");
    expect(q).toMatchObject({ scope: "mine", risk: "A2", state: "EXECUTED", q: "01a1", cursor: "cur|1" });
    expect(q.created_from).toBe(new Date("2026-11-01T00:00:00").toISOString()); // the start of the viewer's day, whatever their zone
    expect(inboxQuery("history", { ...noFilters, risk: "A3" }).risk).toBeUndefined(); // an unknown level is not sent
    expect(hasFilters(noFilters)).toBe(false);
    expect(hasFilters({ ...noFilters, q: " " })).toBe(false);
    expect(hasFilters({ ...noFilters, state: "EXPIRED" })).toBe(true);
  });

  it("announces the counts in words, for the tab shown and for the others", () => {
    expect(countsAnnouncement({ review: 3, mine: 1, history: 7 }, "review", 2)).toBe(
      "To review: showing 2 of 3. To review 3, submitted by me 1, history 7.",
    );
    expect(countsAnnouncement(undefined, "mine", 0)).toBe("");
  });

  it("gives every state a word and never relies on tone alone", () => {
    expect(statusTone("EXECUTED")).toBe("ok");
    expect(statusTone("NEEDS_CHANGES")).toBe("warn");
    expect(statusTone("REJECTED")).toBe("danger");
    expect(statusTone("APPROVED_READY")).toBe("info");
    expect(statusTone("EXPIRED")).toBe("neutral");
  });
});

describe("the decision", () => {
  it("binds the passkey to this request, this version hash and this vote, and asks S2 only for a critical one", () => {
    expect(stepUpDraft(base, "APPROVE")).toEqual({
      action: "execute-sensitive-approval-rejection",
      resource: "approval-case:01a1-case",
      change: { case_id: "01a1-case", request_hash: "a".repeat(64), vote: "APPROVE" },
      critical: true,
    });
    expect(stepUpDraft(withCase({ tier: "A1" }), "REJECT").critical).toBe(false);
  });

  it("needs step-up to approve or reject, never to ask for changes, and a reason to reject or ask for changes", () => {
    expect(needsStepUp("APPROVE") && needsStepUp("REJECT")).toBe(true);
    expect(needsStepUp("REQUEST_CHANGES")).toBe(false);
    expect([needsReason("APPROVE"), needsReason("REJECT"), needsReason("REQUEST_CHANGES")]).toEqual([false, true, true]);
  });

  it("makes one key per intention so a double click or a retry is one decision", () => {
    expect(decisionKey("c1", "APPROVE", "t1")).toBe(decisionKey("c1", "APPROVE", "t1"));
    expect(decisionKey("c1", "APPROVE", "t1")).not.toBe(decisionKey("c1", "REJECT", "t1"));
    expect(decisionKey("c".repeat(200), "APPROVE", "t1").length).toBeLessThanOrEqual(120);
  });

  it("reads a stale version as a conflict to refresh, a missing If-Match, and a plain refusal", () => {
    expect(refusalKind("VERSION_CONFLICT", 409)).toBe("conflict");
    expect(refusalKind("APPROVAL_HASH_MISMATCH", 409)).toBe("conflict");
    expect(refusalKind("PRECONDITION_REQUIRED", 428)).toBe("needs_version");
    expect(refusalKind("VOTER_IS_REQUESTER", 403)).toBe("refused");
    expect(refusalKind("APPROVAL_EXPIRED", 410)).toBe("refused");
    expect(refusalKind("SOMETHING_ELSE", 500)).toBe("other");
    expect(CONFLICT_MESSAGE).toMatch(/Nothing was recorded/);
  });

  it("shows the signed delta: immutable facts first, then the proposed change, sorted", () => {
    const { facts, change } = deltaRows(base);
    expect(facts.map((f) => f.label)).toEqual(["Request id", "Version", "Requested by", "Level", "Policy version", "Data region", "Expires", "Justification"]);
    expect(facts.find((f) => f.label === "Version")?.value).toBe("2");
    expect(change).toEqual([
      { label: "principal id", value: "p-new" },
      { label: "role", value: "OWNER" },
    ]);
  });

  it("lets the server's permission decide what is enabled", () => {
    expect(canDecide(base)).toBe(true);
    expect(canDecide(withCase({ you: { may_vote: false, reasons: ["VOTER_IS_REQUESTER"], is_requester: true } }))).toBe(false);
    expect(canDecide(withCase({ state: "APPROVED_READY" }))).toBe(false); // no vote on a request that already has its quorum
    expect(canRequestChanges(base)).toBe(true);
    expect(canRequestChanges(withCase({ you: { may_vote: true, reasons: [], is_requester: true } }))).toBe(false); // the requester revises
    expect(canApply(withCase({ state: "APPROVED_READY" }))).toBe(true);
    expect(canApply(withCase({ state: "APPROVED_READY", execution_hold: "POLICY_CHANGED" }))).toBe(false); // held: nothing runs
    expect(canApply(withCase({ state: "FAILED" }))).toBe(true);
    expect(canResubmit(withCase({ state: "NEEDS_CHANGES", you: { may_vote: false, reasons: [], is_requester: true } }))).toBe(true);
    expect(canResubmit(withCase({ state: "EXECUTED", you: { may_vote: false, reasons: [], is_requester: true } }))).toBe(false);
  });

  it("explains a hold in words and never as a bare code", () => {
    expect(holdWords(null)).toBeNull();
    expect(holdWords("POLICY_CHANGED")).toMatch(/policy .* changed/i);
    expect(holdWords("INCOMING_OWNER_STRONG_AUTHENTICATOR")).toMatch(/passkey/);
    expect(holdWords("NEW_UNKNOWN_CODE")).toBe("The change is held: new unknown code.");
  });
});

describe("the setup checklist", () => {
  it("states each readiness in plain words, restricted only for a declined check", () => {
    expect(Object.keys(READINESS_WORDS)).toEqual(["DRAFT", "LIMITED_SETUP", "READY_FOR_ACTIVATION", "ACTIVE", "ACTIVE_LIMITED", "RESTRICTED"]);
    expect(READINESS_WORDS.RESTRICTED.tone).toBe("danger");
    expect(READINESS_WORDS.ACTIVE_LIMITED.body).toMatch(/no longer holds/);
    for (const w of Object.values(READINESS_WORDS)) expect(w.title.toLowerCase()).not.toContain("approved"); // never "Approved" for a registry match
  });

  it("maps the five status words to a tone, and 'Verified' only to ok", () => {
    expect(itemTone({ status: "Verified" })).toBe("ok");
    expect(itemTone({ status: "Restricted" })).toBe("danger");
    expect(itemTone({ status: "Needs action" })).toBe("warn");
    expect(itemTone({ status: "In review" })).toBe("info");
    expect(itemTone({ status: "Required" })).toBe("neutral");
  });

  it("sends each unfinished item to a real next step and never to a payment or plan page", () => {
    for (const k of ["company", "authority", "risk", "person_assurance", "privileged_account", "approvers"]) {
      const href = remediationHref(k);
      expect(href).toBeTruthy();
      expect(href).not.toMatch(/billing|plan|pay|upgrade/);
    }
    expect(remediationHref("controls")).toBeNull();
    expect(remediationKeyFor("Authority to represent the company: This check has not been completed.")).toBe("authority");
    expect(remediationKeyFor("Your identity: In review")).toBe("person_assurance");
    expect(remediationKeyFor("Something else")).toBeNull();
  });

  it("shows the server's blocker reasons, or the checklist's own when there is no detail", () => {
    expect(blockerLines([{ field: "blocker", issue: "Screening: held" }, { field: "x", issue: "ignored" }], ["fallback"])).toEqual(["Screening: held"]);
    expect(blockerLines(undefined, ["fallback"])).toEqual(["fallback"]);
    expect(SOLO_MESSAGE).toBe("You can manage your organization on your own. Certain high-risk actions need independent approval.");
  });
});

describe("documents on a request (AT-12)", () => {
  it("lists the kinds a requester can name, with plain labels", () => {
    expect(ATTACHMENT_KINDS.map((k) => k.id)).toEqual(["SUPPORTING_DOCUMENT", "AUTHORITY_DOCUMENT", "IDENTITY_REPROOF_RECORD", "OTHER"]);
    expect(ATTACHMENT_KINDS.every((k) => k.label.length > 0 && !k.label.includes("_"))).toBe(true);
  });

  it("stops what the browser can see: empty, too large, or not a PDF, PNG or JPEG", () => {
    expect(attachmentProblem({ size: 0, type: "application/pdf" })).toMatch(/empty/);
    expect(attachmentProblem({ size: 10 * 1024 * 1024 + 1, type: "application/pdf" })).toMatch(/10 MB/);
    expect(attachmentProblem({ size: 10, type: "application/zip" })).toMatch(/PDF, PNG or JPEG/);
    expect(attachmentProblem({ size: 10, type: "application/pdf" })).toBeNull();
    expect(attachmentProblem({ size: 10, type: "" })).toBeNull(); // the server judges the file's own bytes
  });

  it("explains every refusal in words and never shows a raw code", () => {
    for (const code of ["ATTACHMENT_INFECTED", "ATTACHMENT_RESIDENCY_DENIED", "ATTACHMENT_NOT_PERMITTED", "ATTACHMENT_EXPIRED", "ATTACHMENT_SCAN_UNAVAILABLE"]) {
      expect(attachmentMessage(code)).not.toContain("ATTACHMENT_");
    }
    expect(attachmentMessage("SOMETHING_NEW")).toBe("That document could not be added.");
    expect(attachmentMessage(undefined)).toBe("That document could not be added.");
  });

  it("makes one key per file choice and sizes in plain units", () => {
    const a = attachmentKey("0123456789abcdef", { size: 5 }, "x1");
    expect(a).toBe(attachmentKey("0123456789abcdef", { size: 5 }, "x1"));
    expect(a).not.toBe(attachmentKey("0123456789abcdef", { size: 6 }, "x1"));
    expect(a.length).toBeLessThanOrEqual(120);
    expect(attachmentSize(100)).toBe("1 KB");
    expect(attachmentSize(3 * 1024 * 1024)).toBe("3.0 MB");
  });
});
