import type { MethodLevel } from "@zoiko/api-client";

export const METHOD_NAME: Record<string, string> = {
  CHIP_PASSIVE_AUTHENTICATION: "Passport chip: signed data and issuer chain",
  CHIP_CLONE_CHECK: "Passport chip: clone check",
  PRINTED_DOCUMENT_QUALITY: "Printed document: image quality",
  PRINTED_DOCUMENT_AUTHENTICITY: "Printed document: authenticity",
  FACE_AND_LIVENESS_ENGINE: "Face match and liveness",
  DEVICE_ATTESTATION: "Device attestation",
};

export const LEVEL_WORDS: Record<MethodLevel, { label: string; tone: "ok" | "warn" | "danger" | "neutral"; meaning: string }> = {
  NOT_BUILT: { label: "Not built", tone: "danger", meaning: "No result is produced, so nothing can be approved on it." },
  BUILT_UNPROVEN: { label: "Built, not proven", tone: "warn", meaning: "The software exists and is tested on fixtures. It has never met a real document, a real device or a lab." },
  EVIDENCE_INCOMPLETE: { label: "Records missing", tone: "warn", meaning: "It is on or waiting for approval and named records are missing." },
  APPROVED: { label: "Approved", tone: "ok", meaning: "Every record its approval needs is present." },
};

export function methodName(id: string): string {
  return METHOD_NAME[id] ?? id.toLowerCase().replaceAll("_", " ");
}

/** Methods that still have something to do come first, so the page leads with what blocks a launch. */
export function ordered<T extends { level: MethodLevel }>(rows: T[]): T[] {
  const rank: Record<MethodLevel, number> = { NOT_BUILT: 0, EVIDENCE_INCOMPLETE: 1, BUILT_UNPROVEN: 2, APPROVED: 3 };
  return [...rows].sort((a, b) => rank[a.level] - rank[b.level]);
}
