/** WF08: a verification result is separate statements, never one checkmark. */
import { cleanup, render, screen } from "@testing-library/react";
import type { VerificationAxes } from "@zoiko/api-client";
import { afterEach, describe, expect, it } from "vitest";

import { VerificationResult } from "./receipt-results";
import { axisLines, bannerTone, reasonLine } from "./verification-axes";

afterEach(cleanup);

const good: VerificationAxes = {
  signature_integrity: "VALID",
  issuer_trust: "TRUSTED",
  proof_completeness: "COMPLETE",
  time_proof: "NONE",
  status_at_decision: "NOT_ASSESSED",
  status_now: "NOT_CHECKED",
  status_freshness: "UNKNOWN",
  real_world_claim_verification: "NOT_ASSESSED",
  execution_status: "NOT_APPLICABLE",
  reasons: ["NO_EXTERNAL_TIMESTAMP"],
  caveat: "A valid signature proves the record is intact and who signed it.",
  success: null,
};
const base = { receipt_id: "r1", ok: true, receipt_hash_valid: true, ledgered: true, sealed: true, checks: [{ name: "record_hash", ok: true, detail: "" }] };

describe("Verification axes", () => {
  it("never adds the axes up and always says correctness was not assessed", () => {
    const lines = axisLines(good);
    expect(lines.map((l) => l.label)).toEqual(["Integrity", "Signer", "Proof", "Time", "Status now", "Was the decision right?"]);
    expect(lines.at(-1)).toMatchObject({ value: "not assessed here", tone: "neutral" });
    expect(lines.find((l) => l.label === "Time")?.value).toBe("no outside timestamp");
    expect(lines.find((l) => l.label === "Status now")?.tone).toBe("neutral");
  });

  it("is green only when the receipt is intact and the signer is one you trust", () => {
    expect(bannerTone(good, true)).toBe("ok");
    expect(bannerTone({ ...good, issuer_trust: "UNTRUSTED" }, true)).toBe("danger");
    expect(bannerTone({ ...good, signature_integrity: "INVALID" }, false)).toBe("danger");
    expect(bannerTone({ ...good, signature_integrity: "INDETERMINATE", issuer_trust: "UNKNOWN" }, false)).toBe("info");
    expect(bannerTone(good, false)).toBe("warn");
    expect(bannerTone(undefined, true)).toBeUndefined();
  });

  it("explains each reason in words and shows an unknown one as written", () => {
    expect(reasonLine("KEYS_FROM_THE_BUNDLE")).toContain("vouches for itself");
    expect(reasonLine("CHECK_FAILED:record_hash")).toBe("A check failed: record hash.");
    expect(reasonLine("SOMETHING_NEW")).toBe("SOMETHING_NEW");
  });

  it("shows the separate statements and the caveat next to the integrity result", () => {
    render(<VerificationResult verification={{ ...base, axes: good } as never} />);
    expect(screen.getByText(/^Integrity verified/)).toBeTruthy();
    expect(screen.getByText(/Was the decision right\?:/)).toBeTruthy();
    expect(screen.getByText(/not assessed here/)).toBeTruthy();
    expect(screen.getByText(/No outside timestamp backs the time/)).toBeTruthy();
    expect(screen.getByText(/proves the record is intact and who signed it/)).toBeTruthy();
  });

  it("an untrusted signer is a failure on its own axis, even when the receipt is intact", () => {
    render(<VerificationResult verification={{ ...base, axes: { ...good, issuer_trust: "UNTRUSTED", reasons: ["KEYS_FROM_THE_BUNDLE"] } } as never} />);
    expect(screen.getByText(/The signing keys came with the file itself/)).toBeTruthy();
    expect(screen.getByText(/not a key you trust/)).toBeTruthy();
  });
});
