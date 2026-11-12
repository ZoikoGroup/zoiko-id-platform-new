/** Wire shapes of the two existing, read-only cell endpoints the guide uses (both documented as "navigation only"):
 *   GET /v1/roles/matrix            the published permission matrices (a catalogue, never an enforcement control)
 *   GET /v1/tenant/me/permissions   the signed-in person's live roles and what they could do, if every other condition held
 * The guide reads nothing else and changes nothing. */

/** X execute within approved scope, S prepare or request, A independent approval, R scoped read, T own account only, - denied. */
export type Mode = "X" | "S" | "A" | "R" | "T" | "-";

export interface MatrixAction {
  action: string;
  feature: string;
  permissions: Record<string, Mode>;
  rule: string;
}

export interface MatrixGroup {
  matrix: string;
  title: string;
  actions: MatrixAction[];
}

export interface RoleMatrix {
  version: string;
  policy_hash: string;
  roles: string[];
  matrices: MatrixGroup[];
}

export interface MyPermissions {
  roles: string[];
  policy_hash: string;
  enforcement: boolean;
  rights: Record<string, string[]>;
}

export type GuideDecision = "ANSWER" | "ABSTAIN" | "REFUSE";

export interface GuideLink {
  id: string;
  label: string;
}

export interface GuideAnswer {
  decision: GuideDecision;
  /** Which kind of question this was, for tests and the packet's intent id. */
  intent: string;
  text: string;
  links: GuideLink[];
  /** Matrix action ids the answer is about (cited as the source). */
  actions: string[];
  /** True when the question contained something that looks like a secret; the text is never echoed. */
  sensitive?: boolean;
}

export interface GuideContext {
  matrix: RoleMatrix;
  /** The caller's live roles (from /v1/tenant/me/permissions). Empty means a plain member with no role from the matrix. */
  roles: string[];
  /** Whether the owners have the matrix enforced on routes (`enforcement` of /v1/tenant/me/permissions). False means report-only. */
  enforced?: boolean;
}
