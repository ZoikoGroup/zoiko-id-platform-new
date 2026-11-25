import type { ReceiptVerification, ReplayOutcome } from "@zoiko/api-client";
import { Banner } from "@zoiko/ui";

import { axisLines, bannerTone, reasonLine } from "./verification-axes";

/** Integrity (UX-14): is this the receipt that was committed, in the ledger, under a signed checkpoint? It says nothing
 * about whether the decision was right: that is the replay's job, shown separately. */
export function VerificationResult({ verification }: { verification: ReceiptVerification }) {
  return (
    <Banner
      tone={bannerTone(verification.axes, verification.ok) ?? (verification.ok ? "ok" : verification.ledgered && !verification.sealed ? "info" : "danger")}
      title={
        verification.ok
          ? "Integrity verified: receipt, ledger entry, signed checkpoint and inclusion proof"
          : !verification.ledgered
            ? "Integrity: committed, not yet in the ledger"
            : !verification.sealed
              ? "Integrity: in the ledger, not yet sealed by a checkpoint"
              : "Integrity verification failed"
      }
    >
      {verification.axes ? (
        <div className="mt-2 space-y-2 text-xs">
          <ul aria-label="What was checked, one statement each" className="grid gap-x-4 gap-y-0.5 sm:grid-cols-2">
            {axisLines(verification.axes).map((l) => (
              <li key={l.label}>
                <span className="font-medium">{l.label}:</span> {l.value}
              </li>
            ))}
          </ul>
          {verification.axes.reasons.length ? (
            <ul className="list-disc pl-4">
              {verification.axes.reasons.map((r) => (
                <li key={r}>{reasonLine(r)}</li>
              ))}
            </ul>
          ) : null}
          <p className="text-ink-muted">{verification.axes.caveat}</p>
        </div>
      ) : null}
      <ul className="mt-1 space-y-0.5 text-xs">
        {verification.checks.map((c) => (
          <li key={c.name}>
            {c.ok ? "✓" : "✗"} {c.name}
            {c.detail ? ` — ${c.detail}` : ""}
          </li>
        ))}
      </ul>
    </Banner>
  );
}

const REPLAY_TONE: Record<string, "ok" | "warn" | "danger" | "info"> = {
  VERIFIED_MATCH: "ok",
  VERIFIED_STRUCTURAL_ONLY: "info",
  INCOMPLETE_BY_LAWFUL_ERASURE: "warn",
  MISMATCH: "danger",
  UNVERIFIABLE_INTEGRITY_FAILURE: "danger",
};

/** Correctness (UX-14): would the historic policy reach the same decision today? A receipt can be intact and still
 * mismatch, or fail integrity and so be unreplayable; the two answers are never merged into one. */
export function ReplayResult({ replay }: { replay: ReplayOutcome }) {
  return (
    <Banner tone={REPLAY_TONE[replay.result] ?? "info"} title={`Replay: ${replay.result.replaceAll("_", " ").toLowerCase()}`}>
      {replay.detail ?? (replay.differences?.length ? `Differs in: ${replay.differences.join(", ")}` : "Replayed against the historic policy release.")}
    </Banner>
  );
}
