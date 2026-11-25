import { describe, expect, it } from "vitest";

import { declaredComplete, launchToken, returnHref, screenFor, step, type Status } from "./flow";

const TOKEN = `${"0f1e2d3c4b5a69788796a5b4c3d2e1f0"}.${"a".repeat(43)}`;

function status(over: Partial<Status> = {}): Status {
  return {
    session_ref: "vs_0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0",
    reference: "ZID-0F1E2D3C4B",
    state: "CREATED",
    acknowledged: false,
    biometric_consent: false,
    method: null,
    submitted: false,
    final: false,
    action: null,
    expires_at: null,
    mode: "sandbox",
    ...over,
  };
}

describe("the hosted verification flow (WF-01)", () => {
  it("takes the one-use token only from the fragment, and only in its own shape", () => {
    expect(launchToken(`#t=${TOKEN}`)).toBe(TOKEN);
    expect(launchToken("#t=../../etc")).toBeNull();
    expect(launchToken("")).toBeNull();
  });

  it("chooses every screen from the server's state, and never shows a result before submission", () => {
    expect(screenFor(status())).toBe("welcome");
    expect(screenFor(status({ state: "NOTICE_PRESENTED", acknowledged: true }))).toBe("choose");
    expect(screenFor(status({ state: "METHOD_SELECTED" }))).toBe("capture");
    expect(screenFor(status({ state: "CAPTURING" }))).toBe("review-submit");
    expect(screenFor(status({ state: "CAPTURING", action: { code: "CAPTURE_QUALITY", category: "USER_CORRECTABLE", message: "Retake" } }))).toBe("capture");
    expect(screenFor(status({ state: "MANUAL_REVIEW" }))).toBe("processing");
    expect(screenFor(status({ state: "RESUBMISSION_REQUESTED" }))).toBe("capture");
    expect(screenFor(status({ state: "FINAL_APPROVED" }))).toBe("finished");
    expect(screenFor(status({ state: "EXPIRED" }))).toBe("ended");
    expect(step("choose")).toEqual({ at: 2, of: 4 });
    expect(step("processing")).toBeNull();
  });

  it("returns only to the address the server gave, with the session and the business's state", () => {
    expect(returnHref(status())).toBeNull();
    expect(returnHref(status({ return_url: "https://shop.example/done", return_state: "abc" }))).toBe(
      "https://shop.example/done?session_ref=vs_0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0&state=abc",
    );
  });

  it("asks for every typed detail before sending on the automatic route", () => {
    expect(declaredComplete({ surname: "Mueller", given_names: "Erika", document_number: "C01X00T47", birth_date: "1985-03-12" })).toBe(true);
    expect(declaredComplete({ surname: "Mueller", given_names: "", document_number: "C01", birth_date: "1985-03-12" })).toBe(false);
  });
});
