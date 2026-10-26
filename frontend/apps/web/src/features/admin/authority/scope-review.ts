/** UX-07: a grant that is wide (a wildcard action, every resource of a type, or no limit at all) is never issued by
 * accident. The form shows why it is wide and will not submit until the person confirms they reviewed it. The cell
 * still refuses a grant wider than its parent (GRANT_SCOPE_WIDENING), so this is the human half of the control. */

export type BreadthFinding = { code: "WILDCARD_ACTION" | "ALL_OF_A_TYPE" | "EVERY_RESOURCE" | "NO_LIMIT" | "DEEP_DELEGATION"; message: string };

export function scopeBreadth(input: {
  actions: string[];
  resources: string[];
  hasValueLimit: boolean;
  hasCountLimit: boolean;
  delegate: boolean;
  depth: number;
}): BreadthFinding[] {
  const found: BreadthFinding[] = [];
  if (input.actions.some((a) => a.trim() === "*" || a.trim().endsWith(".*"))) {
    found.push({ code: "WILDCARD_ACTION", message: "An action with * covers every action under it, including ones added later." });
  }
  if (input.resources.some((r) => r.trim() === "*")) {
    found.push({ code: "EVERY_RESOURCE", message: "A bare * covers every resource of every type." });
  } else if (input.resources.some((r) => r.trim().endsWith(":*"))) {
    found.push({ code: "ALL_OF_A_TYPE", message: "type:* covers every resource of that type, now and in future." });
  }
  if (!input.hasValueLimit && !input.hasCountLimit) {
    found.push({ code: "NO_LIMIT", message: "There is no value limit and no count limit." });
  }
  if (input.delegate && input.depth > 1) {
    found.push({ code: "DEEP_DELEGATION", message: "The grantee may pass this on more than one level down." });
  }
  return found;
}

/** The message that stops submission, or null when the scope is narrow or the person has confirmed the review. */
export function reviewRequired(findings: BreadthFinding[], acknowledged: boolean): string | null {
  if (!findings.length || acknowledged) return null;
  return "This grant is broad. Read why under “Review before issuing”, then confirm you reviewed it.";
}
