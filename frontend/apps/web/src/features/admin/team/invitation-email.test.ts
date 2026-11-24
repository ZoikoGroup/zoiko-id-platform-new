import { describe, expect, it } from "vitest";

import { emailNotice, emailProblem } from "./invitation-email";

describe("what the inviter reads about the email", () => {
  it("says nothing when no email was asked for", () => {
    expect(emailNotice(undefined)).toBeNull();
  });

  it("confirms a sent email with the masked address, never a code", () => {
    const n = emailNotice({ status: "SENT", to: "a***@example.com" });
    expect(n?.tone).toBe("ok");
    expect(n?.body).toContain("a***@example.com");
  });

  it("gives a plain sentence and a next step for every way it can go wrong", () => {
    for (const status of ["NOT_CONFIGURED", "DISABLED", "INVALID_ADDRESS", "THROTTLED", "SUPPRESSED", "FAILED", "RENDER_FAILED", "SOMETHING_NEW"]) {
      const n = emailNotice({ status });
      expect(n, status).not.toBeNull();
      expect(n?.tone, status).not.toBe("ok");
      expect(`${n?.title} ${n?.body}`, status).not.toMatch(/[A-Z]{3,}_[A-Z]{3,}/); // no raw status code reaches the person
    }
  });

  it("tells the person to share the link themselves when email is off", () => {
    expect(emailNotice({ status: "NOT_CONFIGURED" })?.body).toMatch(/share it yourself/);
  });

  it("says an unusable address was not sent", () => {
    expect(emailNotice({ status: "INVALID_ADDRESS" })?.tone).toBe("danger");
  });
});

describe("the address check", () => {
  it("accepts one plain address", () => {
    expect(emailProblem("asha.rao@example.com")).toBeNull();
    expect(emailProblem("  a@b.co  ")).toBeNull();
  });
  it("refuses empty, several, display-name and malformed addresses", () => {
    expect(emailProblem("")).toMatch(/Type the person/);
    expect(emailProblem("a@b.com, c@d.com")).toMatch(/one plain address/);
    expect(emailProblem("Name <a@b.com>")).toMatch(/one plain address/);
    expect(emailProblem("a@b.com;c@d.com")).toMatch(/one plain address/);
    expect(emailProblem("not-an-address")).toMatch(/does not look like/);
    expect(emailProblem("a@b")).toMatch(/does not look like/);
    expect(emailProblem(`${"a".repeat(250)}@b.com`)).toMatch(/too long/);
  });
});
