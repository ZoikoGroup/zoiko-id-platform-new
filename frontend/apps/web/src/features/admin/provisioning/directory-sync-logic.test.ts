/** What a directory sync tells the person: its last run in words, and every failure code explained, never provider text. */
import { describe, expect, it } from "vitest";

import { INTERVALS, countsSummary, isAuth0Domain, syncErrorMessage } from "./directory-sync-logic";

describe("directory sync logic", () => {
  it("summarises the last run in words and skips what is zero", () => {
    expect(countsSummary({ created: 3, updated: 1, unchanged: 12, deactivated: 1, failed: 0 })).toBe("3 created, 1 updated, 12 unchanged, 1 deactivated");
    expect(countsSummary({ created: 0, updated: 0, unchanged: 0, deactivated: 0, failed: 2 })).toBe("2 refused");
    expect(countsSummary({})).toBe("no changes");
  });

  it("explains each failure code the cell can record, and says nothing for a clean run", () => {
    for (const code of [
      "CREDENTIAL_REJECTED", "CREDENTIAL_NOT_ALLOWED", "CREDENTIAL_TOO_BROAD", "CREDENTIAL_MISSING", "DIRECTORY_TOO_LARGE", "DIRECTORY_INCOMPLETE",
      "EMPTY_DIRECTORY", "MASS_DEACTIVATION", "PROVIDER_UNREACHABLE", "PROVIDER_RATE_LIMITED", "PROVIDER_ERROR", "PROVIDER_ADDRESS_REFUSED",
      "SOURCE_NOT_ACTIVE", "SOME_PEOPLE_REFUSED", "SYNC_ERROR",
    ]) {
      expect(syncErrorMessage(code)?.length ?? 0).toBeGreaterThan(20);
    }
    expect(syncErrorMessage(null)).toBeNull();
    expect(syncErrorMessage("SOMETHING_NEW")).toBe("The last run did not complete.");
    expect(syncErrorMessage("CREDENTIAL_TOO_BROAD")).toContain("read:users");
  });

  it("accepts only an Auth0 tenant address", () => {
    expect(isAuth0Domain("dev-qogbbz4c306u41k1.us.auth0.com")).toBe(true);
    expect(isAuth0Domain(" acme.eu.auth0.com ")).toBe(true);
    for (const bad of ["", "169.254.169.254", "evil.example.com", "acme.auth0.com.evil.example", "https://acme.us.auth0.com", "auth0.com"]) {
      expect(isAuth0Domain(bad)).toBe(false);
    }
  });

  it("offers intervals the cell accepts (five minutes to a day)", () => {
    for (const i of INTERVALS) expect(i.seconds >= 300 && i.seconds <= 86400).toBe(true);
  });
});
