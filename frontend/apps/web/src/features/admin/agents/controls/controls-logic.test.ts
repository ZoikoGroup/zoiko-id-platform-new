import { describe, expect, it } from "vitest";

import { ACTION_STATE, enforcementText, isMandateId, parsePublicKey, reasonText, signingText, stopProblem, VERDICT } from "./controls-logic";

describe("Agent controls", () => {
  it("asks for what each kind of stop needs", () => {
    expect(stopProblem("TENANT", "", "Emergency")).toBeNull();
    expect(stopProblem("AGENT", "", "Emergency")).toContain("agent id");
    expect(stopProblem("ISSUER", "idp.example.com", "Key stolen")).toContain("https");
    expect(stopProblem("ISSUER", "https://idp.example.com#key-1", "Key stolen")).toBeNull();
    expect(stopProblem("SPONSOR", "p-1", " ")).toContain("Say why");
  });

  it("never calls a stop complete while a place is not told", () => {
    expect(enforcementText(true, 0)).toContain("every point");
    expect(enforcementText(false, 2)).toContain("2 tool servers are not told");
    expect(enforcementText(false, 1)).toContain("1 tool server is not told");
  });

  it("keeps an unknown outcome apart from success and failure", () => {
    expect(ACTION_STATE.UNKNOWN.tone).toBe("danger");
    expect(ACTION_STATE.EXECUTED.label).toContain("as the tool reported");
    expect(ACTION_STATE.AUTHORIZED.label).toContain("not yet run");
  });

  it("refuses a private key and a key without an id", () => {
    expect(parsePublicKey("nope").problem).toContain("JSON");
    expect(parsePublicKey("[1]").problem).toContain("object");
    expect(parsePublicKey('{"kid":"a","d":"x"}').problem).toContain("private");
    expect(parsePublicKey('{"kty":"EC"}').problem).toContain("key id");
    expect(parsePublicKey('{"kid":"a","kty":"EC"}').key).toEqual({ kid: "a", kty: "EC" });
  });

  it("never reads a credential as permission to act", () => {
    expect(VERDICT.IDENTITY_VERIFIED_AUTHORITY_NOT_EVALUATED.text).toContain("was not checked");
    expect(VERDICT.VALID_BUT_POSSESSION_NOT_SHOWN.tone).toBe("warn");
    expect(VERDICT.NOT_VALID.tone).toBe("danger");
    for (const v of Object.values(VERDICT)) expect(`${v.label} ${v.text}`.toLowerCase()).not.toContain("approved");
    expect(reasonText("AGENT_SUSPENDED")).toContain("stopped");
    expect(reasonText("SOMETHING_NEW")).toBe("SOMETHING_NEW");
  });

  it("says who a mandate still needs", () => {
    expect(signingText({ required: ["GRANTOR"], missing: [], complete: true })).toContain("Every confirmation");
    expect(signingText({ required: ["GRANTOR", "APPROVER"], missing: ["APPROVER"], complete: false })).toContain("independent second person");
    expect(signingText({ required: ["GRANTOR", "APPROVER"], missing: ["GRANTOR", "APPROVER"], complete: false })).toContain("handed on their authority and an independent");
    expect(isMandateId("0b4f3f4e-4f0f-4d4e-8f0e-1234567890ab")).toBe(true);
    expect(isMandateId("mandate-1")).toBe(false);
  });
});
