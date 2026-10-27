/** UX-14: receipt verification reports integrity separately from correctness (the replay result). */
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { ReplayResult, VerificationResult } from "./receipt-results";

afterEach(cleanup);

const verified = {
  receipt_id: "r1",
  ok: true,
  receipt_hash_valid: true,
  ledgered: true,
  sealed: true,
  checks: [{ name: "receipt hash", ok: true }, { name: "checkpoint signature", ok: true }],
};

describe("UX-14 integrity and correctness are separate answers", () => {
  it("an intact receipt can still be a replay mismatch, and each says so on its own", () => {
    render(
      <>
        <VerificationResult verification={verified as never} />
        <ReplayResult replay={{ receipt_id: "r1", replay_id: "p1", result: "MISMATCH", differences: ["outcome"], verification: verified } as never} />
      </>,
    );
    expect(screen.getByText(/^Integrity verified/)).toBeTruthy();
    expect(screen.getByText(/^Replay: mismatch/)).toBeTruthy();
    expect(screen.getByText(/Differs in: outcome/)).toBeTruthy();
  });

  it("each answer is its own element, never one merged verdict", () => {
    const { container } = render(
      <>
        <VerificationResult verification={verified as never} />
        <ReplayResult replay={{ receipt_id: "r1", replay_id: "p1", result: "VERIFIED_MATCH", verification: verified } as never} />
      </>,
    );
    const titles = Array.from(container.querySelectorAll("[role='status'], [role='alert'], [data-banner], div")).map((n) => n.textContent ?? "");
    expect(titles.filter((t) => /^Integrity verified/.test(t)).length).toBeGreaterThan(0);
    expect(titles.filter((t) => /^Replay: verified match/.test(t)).length).toBeGreaterThan(0);
  });

  it("a receipt that is not yet sealed is reported as an integrity state, not as a wrong decision", () => {
    render(<VerificationResult verification={{ ...verified, ok: false, sealed: false } as never} />);
    expect(screen.getByText(/Integrity: in the ledger, not yet sealed/)).toBeTruthy();
  });

  it("a receipt that fails integrity is never presented as replayable evidence", () => {
    render(<VerificationResult verification={{ ...verified, ok: false, receipt_hash_valid: false } as never} />);
    expect(screen.getByText(/Integrity verification failed/)).toBeTruthy();
  });
});
