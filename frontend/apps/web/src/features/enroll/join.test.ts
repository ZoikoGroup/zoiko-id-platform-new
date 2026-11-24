import { describe, expect, it } from "vitest";

import { joinDestination, parseJoinToken } from "./join";

const ticket = "Zx9_kQ2-mP4rT7vB1nL8cD5fH3jS6wYa"; // a ticket can contain underscores and hyphens

describe("the address in an invitation email", () => {
  it("splits the workspace from the ticket at the first underscore, whatever the ticket holds", () => {
    expect(parseJoinToken(`acme_${ticket}`)).toEqual({ workspace: "acme", ticket });
    expect(parseJoinToken(`tesco-plc_${ticket}`)).toEqual({ workspace: "tesco-plc", ticket });
  });

  it("sends the person to the passkey setup page with the workspace named", () => {
    expect(joinDestination(`acme_${ticket}`)).toBe(`/enroll?ticket=${encodeURIComponent(ticket)}&workspace=acme`);
  });

  it("sends anything unreadable to sign-in, never to an error and never off the site", () => {
    for (const bad of [undefined, "", "noseparator", "_onlyticket1234", "UPPER_" + ticket, "acme_", "acme_short", "acme_" + "x".repeat(600), "ac me_" + ticket, "-acme_" + ticket, "acme-_" + ticket]) {
      expect(joinDestination(bad), String(bad)).toBe("/sign-in");
    }
    expect(joinDestination("https://evil.example/_" + ticket)).toBe("/sign-in");
    expect(joinDestination(`acme_${ticket}&next=https://evil.example`)).toBe("/sign-in"); // an extra parameter is not a valid ticket
  });

  it("encodes what it puts in the address", () => {
    const d = joinDestination(`acme_${ticket}`);
    expect(d.startsWith("/")).toBe(true);
    expect(d).not.toContain("//");
  });
});
