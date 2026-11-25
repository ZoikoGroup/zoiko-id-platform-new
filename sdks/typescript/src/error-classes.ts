import table from "./error-classes.json";

/** The documented class of a Verify API error code, the same table every Zoiko SDK ships (ZID-CP-WF10, WF-10.05). */
export type ErrorClassEntry = { class: string; retryable: boolean };

const CODES = table.codes as Record<string, ErrorClassEntry>;

export function errorClassOf(code: string): ErrorClassEntry | undefined {
  return CODES[code];
}

export const ERROR_CLASSES: readonly string[] = table.classes;
