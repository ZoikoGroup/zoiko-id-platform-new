import type { OrgClaimCheckState, OrgClaimHolding, OrgClaimOutcome, OrgClaimParty, OrgClaimScreening, OrgClaimStatus } from "@zoiko/api-client";

/** The four independent checks of WF-13B-03, in the order they are shown. A found company is not a verified person,
 * a verified person is not an authorized one, and none of it is a cleared risk review. */
export const CHECKS: { key: keyof OrgClaimStatus["checks"]; label: string; help: string }[] = [
  { key: "company", label: "Company and legal status", help: "The company exists and is active in an official source." },
  { key: "person_assurance", label: "Your identity", help: "Your identity is verified in its own step; signing up or a matching email is not proof." },
  { key: "authority", label: "Your authority to act for it", help: "You are a current officer of the company, or hold a valid delegation." },
  { key: "risk", label: "Risk and ownership review", help: "Directors, owners and sanctions checks the country requires." },
];

export const CHECK_TONE: Record<OrgClaimCheckState, "ok" | "info" | "warn" | "danger"> = {
  COMPLETED: "ok",
  PENDING: "info",
  NEEDS_ACTION: "warn",
  HELD: "warn",
  DECLINED: "danger",
};

export const CHECK_WORDS: Record<OrgClaimCheckState, string> = {
  COMPLETED: "Completed",
  PENDING: "Pending",
  NEEDS_ACTION: "Needs action",
  HELD: "In review",
  DECLINED: "Declined",
};

/** The one outcome, in plain words and with what to do next. Never promises a time, and never calls a register match "verified". */
export function outcomeCopy(outcome: OrgClaimOutcome): { title: string; body: string; tone: "ok" | "info" | "warn" | "danger" } {
  switch (outcome) {
    case "VERIFIED":
      return { title: "Verified", body: "Every check is complete. Organization roles and credentials that need a verified company are available.", tone: "ok" };
    case "IN_REVIEW":
      return { title: "In review", body: "A person at Zoiko iD is reviewing part of this claim. Nothing is needed from you unless we contact you.", tone: "warn" };
    case "NEEDS_EVIDENCE":
      return { title: "More information needed", body: "Add the details marked Needs action, then check again.", tone: "warn" };
    case "DECLINED":
      return { title: "Declined", body: "This claim could not be verified. You can ask for a review or start a new claim.", tone: "danger" };
    case "STALE":
      return { title: "Out of date", body: "The evidence has expired and must be refreshed.", tone: "warn" };
    case "RESTRICTED":
      return { title: "Restricted", body: "Verified privileges are paused while a review is open.", tone: "warn" };
    default:
      return {
        title: "Provisional",
        body: "The company is recorded as a claim only. It is not a verified organization, and no owner or approver rights follow from it.",
        tone: "info",
      };
  }
}

/** A person's name as the applicant typed it, with the whitespace tidied; nothing else is changed. */
export const tidy = (value: string): string => value.trim().replace(/\s+/g, " ");

/** "25", "12.5" or "100"; anything else is rejected before it is sent, and the server checks again. */
export function pctValid(value: string): boolean {
  const v = value.trim();
  if (!/^\d{1,3}(\.\d{1,4})?$/.test(v)) return false;
  const n = Number(v);
  return n >= 0 && n <= 100;
}

export type OwnerRow = { name: string; birthYear: string; sharesPct: string; votesPct: string; boardControl: boolean };

export function emptyOwner(): OwnerRow {
  return { name: "", birthYear: "", sharesPct: "", votesPct: "", boardControl: false };
}

export function ownerRowErrors(r: OwnerRow): string[] {
  const out: string[] = [];
  if (!tidy(r.name)) out.push("Enter the person's full legal name.");
  if (r.birthYear && !/^(19|20)\d\d$/.test(r.birthYear.trim())) out.push("Enter a four-digit birth year.");
  if (r.sharesPct && !pctValid(r.sharesPct)) out.push("Shares must be between 0 and 100.");
  if (r.votesPct && !pctValid(r.votesPct)) out.push("Votes must be between 0 and 100.");
  if (!r.sharesPct && !r.votesPct && !r.boardControl) out.push("State the shares, the votes or the board control.");
  return out;
}

/** The screening request. Each person gets an id of this form's own; holdings are what the applicant declares, and the server
 * treats them as unverified until a source confirms them, so a declaration never clears anyone. */
export function buildScreening(representativeName: string, owners: OwnerRow[]): OrgClaimScreening {
  const party = (ref: string, name: string, birthYear?: string): OrgClaimParty => ({
    ref,
    kind: "PERSON",
    name: tidy(name),
    birth: birthYear?.trim() || null,
  });
  const people = owners.map((o, i) => party(`owner${i + 1}`, o.name, o.birthYear));
  const holdings: OrgClaimHolding[] = owners.map((o, i) => ({
    owner: `owner${i + 1}`,
    shares_pct: o.sharesPct.trim() || null,
    votes_pct: o.votesPct.trim() || null,
    controls: o.boardControl ? ["APPOINT_BOARD_MAJORITY"] : [],
  }));
  return { representative: party("rep", representativeName), owners: people, holdings };
}

/** One id per opening of the search page: a double click is one claim, and coming back later is a new attempt. */
export function attemptId(): string {
  return typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID().slice(0, 8) : Date.now().toString(36);
}

/** A fresh idempotency key per intention, so a double click is one claim and a second company is a second claim. */
export function claimKey(country: string, registrationNumber: string, attempt: string): string {
  return `claim-${country}-${registrationNumber}-${attempt}`.slice(0, 120);
}

/** What the next step is, from the checks alone. */
export function nextStep(status: OrgClaimStatus): "authority" | "wait" | "done" {
  if (status.outcome === "VERIFIED") return "done";
  if (status.screening === null && status.checks.company === "COMPLETED") return "authority";
  return "wait";
}
