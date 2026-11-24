/** The person's path to a verified company, in the order they do it: their own identity, the company (the certificate of incorporation, read for them), then onboarding.
 * Pure, so the order and the locks are tested without a page. Nothing here marks anything verified: it only reads what the server already decided. */

export type JourneyState = "done" | "current" | "locked";
export type JourneyId = "identity" | "company" | "onboarding";

export type JourneyStep = {
  id: JourneyId;
  title: string;
  summary: string;
  state: JourneyState;
  /** Where the next action goes; null when there is nothing to do here. */
  href: string | null;
  action: string;
};

/** Where identity verification sends the person back to once it is approved. */
export const BACK_TO = "/admin/company-verification";

export function journey(input: { identityDone: boolean; companyVerified: boolean }): JourneyStep[] {
  const { identityDone, companyVerified } = input;
  return [
    {
      id: "identity",
      title: "Verify your identity",
      summary: identityDone
        ? "Your identity is verified."
        : "Photograph an identity document and yourself. We compare the two. This is the first step, and the company check needs it.",
      state: identityDone ? "done" : "current",
      href: identityDone ? null : `/account/identity-proofing?next=${encodeURIComponent(BACK_TO)}`,
      action: "Verify your identity",
    },
    {
      id: "company",
      title: "Verify your company",
      summary: companyVerified
        ? "Your company is verified."
        : identityDone
          ? "Find your company and upload its certificate of incorporation. We read it for you and you confirm what we read."
          : "Finish your identity check first.",
      state: companyVerified ? "done" : identityDone ? "current" : "locked",
      href: identityDone && !companyVerified ? "/admin/organization/claim" : null,
      action: "Verify your company",
    },
    {
      id: "onboarding",
      title: "Continue to onboarding",
      summary: companyVerified ? "Your company is verified. Set up domains, residency and your applications." : "Opens when your company is verified.",
      state: companyVerified ? "current" : "locked",
      href: companyVerified ? "/admin/onboarding" : null,
      action: "Go to onboarding",
    },
  ];
}

/** The step the person should do now, or null when nothing is left before onboarding. */
export function nextStep(steps: JourneyStep[]): JourneyStep | null {
  return steps.find((s) => s.state === "current") ?? null;
}

/** An identity check counts only once the server has approved it. */
export function identityApproved(cases: readonly { decision: string }[]): boolean {
  return cases.some((c) => c.decision === "APPROVED");
}

const BEFORE_VERIFIED = ["DRAFT", "ORGANIZATION_VERIFYING"];
const AFTER_VERIFIED = ["CONFIGURING", "INTEGRATING", "SHADOW", "PILOT", "ACTIVE"];

/** The company counts as verified once the workspace has moved past company verification. A suspended or closed workspace is not read as verified. */
export function companyVerified(status: string | undefined | null): boolean {
  if (!status || BEFORE_VERIFIED.includes(status)) return false;
  return AFTER_VERIFIED.includes(status);
}
