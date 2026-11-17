export const SIGN_OFF_ROLES = ["COMPLIANCE", "LEGAL", "PRIVACY", "SECURITY"] as const;
export type SignOffRole = (typeof SIGN_OFF_ROLES)[number];

const PROBLEM_TEXT: Record<string, string> = {
  NO_EVIDENCE_ROUTE: "The pack names no source and no documents, so nothing could be checked.",
  TWO_REVIEWERS_REQUIRED_FOR_DOCUMENT_FIRST: "Document-first approval needs two independent reviewers.",
  UBO_THRESHOLD_MISSING: "No ownership threshold is set for this market.",
  UBO_THRESHOLD_NOT_SIGNED: "The ownership threshold is not signed by the market's Compliance owner.",
  CONTROL_BY_OTHER_MEANS_NOT_ASSESSED: "Control by other means (votes, nominees, trusts) is not assessed.",
  RETENTION_LEGAL_BASIS_MISSING: "The retention schedule has no recorded legal basis.",
  LEGAL_HOLD_NOT_SUPPORTED: "The retention schedule does not support legal holds.",
  DELETION_JOB_MISSING: "No deletion job is named.",
  PUBLISH_TESTS_NOT_PASSED: "The pack's own checks have not passed.",
  AUTHOR_CANNOT_APPROVE_OWN_POLICY: "The author signed their own pack; that signature does not count.",
  TWO_PERSON_PUBLISH_REQUIRED: "A change to something high-impact needs two different approvers.",
};

/** A problem code in words for the people who own the control. An unknown code shows as itself. */
export function problemText(code: string): string {
  const known = PROBLEM_TEXT[code];
  if (known) return known;
  let m = /^SIGN_OFF_(COMPLIANCE|LEGAL|PRIVACY|SECURITY)_MISSING$/.exec(code);
  if (m) return `No ${m[1]!.toLowerCase()} sign-off yet.`;
  m = /^SOURCE_(.+)_(LEGAL_BASIS_MISSING|CREDENTIAL_MISSING|TERMS_NOT_RECORDED)$/.exec(code);
  if (m) {
    const what = { LEGAL_BASIS_MISSING: "has no recorded lawful basis", CREDENTIAL_MISSING: "has no credential in the key store", TERMS_NOT_RECORDED: "has no recorded terms of use" }[m[2] as "LEGAL_BASIS_MISSING"];
    return `The source “${m[1]!.toLowerCase().replaceAll("_", " ")}” ${what}.`;
  }
  m = /^RETENTION_(RAW_DOCUMENTS|EXTRACTED_DATA|DECISION_METADATA|SCREENING_EVIDENCE)_MISSING$/.exec(code);
  if (m) return `No retention period for ${m[1]!.toLowerCase().replaceAll("_", " ")}.`;
  m = /^RECHECK_(K1|K2)_(MISSING|ABOVE_CEILING_NOT_RATIFIED)$/.exec(code);
  if (m) return m[2] === "MISSING" ? `No recheck period for grade ${m[1]}.` : `The recheck period for grade ${m[1]} is longer than the provisional ceiling and is not ratified.`;
  return code;
}

export function groupProblems(codes: string[]): { signOffs: string[]; other: string[] } {
  const signOffs = codes.filter((c) => c.startsWith("SIGN_OFF_"));
  return { signOffs, other: codes.filter((c) => !c.startsWith("SIGN_OFF_")) };
}

/** The roles that have not signed yet, from the roles that have. */
export function rolesStillNeeded(signed: string[]): SignOffRole[] {
  return SIGN_OFF_ROLES.filter((r) => !signed.includes(r));
}

export function shortHash(hash: string): string {
  return hash.replace(/^sha256:/, "").slice(0, 12);
}

const DIGEST = /^sha256:[0-9a-f]{64}$/;

export function isDigest(value: string): boolean {
  return DIGEST.test(value.trim());
}

/** A pack typed or pasted by the author, as JSON, or the reason it cannot be read. The platform checks it again and names what is incomplete. */
export function parsePack(text: string): { ok: true; pack: Record<string, unknown> } | { ok: false; reason: string } {
  try {
    const v: unknown = JSON.parse(text);
    if (typeof v !== "object" || v === null || Array.isArray(v)) return { ok: false, reason: "The pack must be one JSON object." };
    return { ok: true, pack: v as Record<string, unknown> };
  } catch {
    return { ok: false, reason: "That is not valid JSON." };
  }
}

export function rollbackProblem(reason: string): string | null {
  return reason.trim().length >= 10 ? null : "Give a reason of at least ten characters.";
}
