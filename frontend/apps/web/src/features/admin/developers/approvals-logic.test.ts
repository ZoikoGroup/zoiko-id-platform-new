import type { VerifyApiApproval } from "@zoiko/api-client";
import { describe as suite, expect, it } from "vitest";

import { APPROVAL_STATE, askBody, askProblem, canCancel, canDecide, canUse, describe, EMPTY_ASK, hoursLeft } from "./approvals-logic";

const KEY = { kty: "OKP", crv: "Ed25519", x: "abc" };
const a = (over: Partial<VerifyApiApproval> = {}): VerifyApiApproval => ({
  approval_id: "p1",
  application_id: "app1",
  action: "ISSUE",
  credential_id: null,
  with_key: false,
  status: "PENDING",
  requested_by: "u1",
  decided_by: null,
  note: null,
  created_at: "2026-11-25T10:00:00Z",
  expires_at: "2026-11-26T10:00:00Z",
  decided_at: null,
  ...over,
});

suite("production credential approvals", () => {
  it("has words for every state", () => {
    for (const s of ["PENDING", "APPROVED", "CONSUMED", "CANCELED", "REJECTED", "EXPIRED"]) expect(APPROVAL_STATE[s]!.label).toBeTruthy();
  });
  it("lets a waiting request be decided or taken back and only an approved one be used", () => {
    expect(canDecide(a())).toBe(true);
    expect(canCancel(a())).toBe(true);
    expect(canUse(a())).toBe(false);
    expect(canUse(a({ status: "APPROVED" }))).toBe(true);
    for (const status of ["CONSUMED", "CANCELED", "REJECTED", "EXPIRED"] as const) {
      expect(canUse(a({ status }))).toBe(false);
      expect(canDecide(a({ status }))).toBe(false);
    }
  });
  it("says what an approval is for", () => {
    expect(describe(a())).toBe("Create a credential with a secret");
    expect(describe(a({ with_key: true }))).toBe("Create a key credential");
    expect(describe(a({ action: "ROTATE", with_key: true }))).toBe("Rotate a credential to a new key");
    expect(describe(a({ action: "ROTATE" }))).toBe("Rotate a credential to a new secret");
  });
  it("counts the hours left and never goes negative", () => {
    expect(hoursLeft(a(), new Date("2026-11-25T10:00:00Z"))).toBe(24);
    expect(hoursLeft(a(), new Date("2026-11-25T22:30:00Z"))).toBe(11);
    expect(hoursLeft(a(), new Date("2026-11-27T00:00:00Z"))).toBe(0);
  });
  it("checks the request before it is sent and sends only what the approval is for", () => {
    expect(askProblem(EMPTY_ASK)).toBeNull();
    expect(askProblem({ ...EMPTY_ASK, action: "ROTATE" })).toContain("credential to rotate");
    expect(askProblem({ ...EMPTY_ASK, kind: "KEY" })).toContain("public key");
    expect(askProblem({ ...EMPTY_ASK, kind: "KEY", keyText: JSON.stringify({ ...KEY, d: "x" }) })).toContain("private key material");
    expect(askBody(EMPTY_ASK)).toEqual({ action: "ISSUE" });
    expect(askBody({ action: "ROTATE", credentialId: "c1", kind: "KEY", keyText: JSON.stringify(KEY) })).toEqual({ action: "ROTATE", credential_id: "c1", public_jwk: KEY });
  });
});
