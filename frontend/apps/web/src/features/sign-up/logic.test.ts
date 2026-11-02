import type { RegistrationCapabilities } from "@zoiko/api-client";
import { describe, expect, it } from "vitest";

import {
  accountLabels,
  canSubmit,
  countryName,
  countryState,
  COUNTRY_CODES,
  emailLooksValid,
  EXPIRED_MESSAGE,
  filterCountries,
  GENERIC_MESSAGE,
  HOLD_MESSAGE,
  JOURNEYS,
  serverMessage,
  stepView,
  tokenFromQuery,
  validate,
} from "./logic";

const caps = (country: RegistrationCapabilities["country"]): RegistrationCapabilities => ({
  notice_version: "v1",
  locale: "und",
  nonce: "n",
  journeys: ["PERSONAL", "ORGANIZATION"],
  identity_providers: [],
  authenticators: { primary: "PASSKEY", fallback: "GOVERNED_RECOVERY" },
  country,
});

describe("the form's own checks", () => {
  it("offers exactly the two journeys and none is preselected by the rules", () => {
    expect(JOURNEYS.map((j) => j.label)).toEqual(["For myself", "For an organization"]);
    const errors = validate({ journey: null, country: "", email: "" }).map((e) => e.field);
    expect(errors).toEqual(["journey", "country", "email"]); // every field starts unanswered and must be answered
  });

  it("accepts a Unicode address and refuses ones that could smuggle a header or a second recipient", () => {
    expect(emailLooksValid("jürgen.k+tag@bücher.example")).toBe(true);
    expect(emailLooksValid("名前@例え.jp")).toBe(true);
    for (const bad of ["", "a", "a@b", "a@@b.co", "a b@c.co", "a@b..co", "a@c.co\nBcc: x@y.co", "<a>@b.co", "a,b@c.co", `${"x".repeat(65)}@b.co`]) {
      expect(emailLooksValid(bad)).toBe(false);
    }
  });

  it("never defaults a country and only accepts a code from the list", () => {
    expect(validate({ journey: "PERSONAL", country: "ZZ", email: "a@b.co" }).map((e) => e.field)).toEqual(["country"]);
    expect(validate({ journey: "PERSONAL", country: "GB", email: "a@b.co" })).toEqual([]);
    expect(COUNTRY_CODES.length).toBeGreaterThan(240);
  });

  it("filters the country picker by name or code, ignoring case and accents", () => {
    expect(filterCountries("united kingdom").map((c) => c.code)).toContain("GB");
    expect(filterCountries("côte").map((c) => c.code)).toContain("CI");
    expect(filterCountries("gb").map((c) => c.code)).toEqual(["GB"]);
    expect(filterCountries("").length).toBe(COUNTRY_CODES.length);
    expect(countryName("DE", "de")).toBe("Deutschland"); // the display language names the country, it never chooses one
  });
});

describe("what the server said about the chosen country", () => {
  it("lets an available country through and keeps the organization note honest", () => {
    const s = countryState(
      caps({ code: "GB", account: "AVAILABLE", routing: { state: "HERE", url: null }, organization_claims: "NOT_AVAILABLE_YET", message: null }),
      "GB",
    );
    expect(s).toEqual({ kind: "ok", organizationClaims: false });
    expect(canSubmit(s)).toBe(true);
  });

  it("refuses to submit for an unavailable country, with the server's honest message", () => {
    const s = countryState(
      caps({ code: "BR", account: "NOT_AVAILABLE_YET", routing: { state: "UNAVAILABLE", url: null }, organization_claims: "NOT_AVAILABLE_YET", message: "Zoiko iD accounts are not available for this country yet. Nothing has been stored." }),
      "BR",
    );
    expect(s.kind).toBe("unavailable");
    expect(canSubmit(s)).toBe(false);
    expect(s.kind === "unavailable" && s.message).toMatch(/not available for this country yet/);
  });

  it("carries the contact route when the registry has closed a country", () => {
    const s = countryState(
      caps({ code: "DE", account: "NOT_AVAILABLE_YET", routing: { state: "HERE", url: null }, organization_claims: "NOT_AVAILABLE_YET", message: "Zoiko iD accounts are not available for this country yet. Nothing has been stored.", contact: "support@zoikogroup.com" }),
      "DE",
    );
    expect(s.kind === "unavailable" && s.contact).toBe("support@zoikogroup.com");
    expect(canSubmit(s)).toBe(false);
  });

  it("refuses to submit here for a country held in another region", () => {
    const s = countryState(
      caps({ code: "DE", account: "AVAILABLE_IN_ANOTHER_REGION", routing: { state: "OTHER_CELL", url: "https://eu.example.com" }, organization_claims: "AVAILABLE", message: null }),
      "DE",
    );
    expect(s).toEqual({ kind: "other_region", url: "https://eu.example.com" });
    expect(canSubmit(s)).toBe(false);
  });

  it("knows nothing until the server has answered for the country that is chosen now", () => {
    expect(countryState(undefined, "GB").kind).toBe("none");
    expect(countryState(caps(null), "GB").kind).toBe("none");
    const stale = caps({ code: "FR", account: "AVAILABLE", routing: { state: "HERE", url: null }, organization_claims: "AVAILABLE", message: null });
    expect(canSubmit(countryState(stale, "GB"))).toBe(false); // an answer for another country is not an answer
  });
});

describe("the words for each step", () => {
  it("uses the document's exact copy for the generic, expired and held states", () => {
    expect(GENERIC_MESSAGE).toBe("If this address can be used, check your inbox for next steps.");
    expect(EXPIRED_MESSAGE).toBe("This link is no longer valid. Request a new link.");
    expect(HOLD_MESSAGE).toBe("We couldn't complete registration securely. Contact support or try again later.");
    expect(serverMessage("LINK_INVALID")).toBe(EXPIRED_MESSAGE);
    expect(serverMessage("SOMETHING_INTERNAL")).not.toMatch(/SOMETHING_INTERNAL/); // a raw code never reaches the person
  });

  it("maps every next step the server can name, without ever saying whose account a link was", () => {
    const steps = ["VERIFY_EMAIL", "ENROLL_AUTHENTICATOR", "PERSONAL_WORKSPACE", "ORGANIZATION_CLAIM", "ACCEPT_INVITATION", "REQUEST_NEW_LINK", "CONTACT_SUPPORT"] as const;
    for (const next_step of steps) {
      const v = stepView({ next_step, message: null });
      expect(v.title.length).toBeGreaterThan(0);
      expect(`${v.title} ${v.body}`.toLowerCase()).not.toMatch(/already (have|registered)|belongs to|owner of this/);
    }
    expect(stepView({ next_step: "ORGANIZATION_CLAIM", message: null }).action?.href).toBe("/admin/organization/claim");
    expect(stepView({ next_step: "REQUEST_NEW_LINK", message: null }).resend).toBe(true);
    expect(stepView({ next_step: "VERIFY_EMAIL", message: null }).resend).toBe(true);
    expect(stepView({ next_step: "CONTACT_SUPPORT", message: null }).body).toBe(HOLD_MESSAGE);
    expect(stepView({ next_step: "REQUEST_NEW_LINK", message: null }).body).toBe(EXPIRED_MESSAGE);
    expect(stepView({ next_step: "ENROLL_AUTHENTICATOR", message: null }).body).toMatch(/never accept email or SMS codes/);
  });

  it("keeps 'account active' apart from 'identity not verified'", () => {
    expect(accountLabels({ account: "ACTIVE", identity: "NOT_VERIFIED" })).toEqual({ account: "Account active", identity: "Identity not verified" });
    expect(accountLabels({ account: "NONE", identity: "NOT_VERIFIED" }).account).toBe("No account yet");
  });
});

describe("the emailed link", () => {
  it("reads only a well-formed token and ignores everything else in the URL", () => {
    const token = "A".repeat(43);
    expect(tokenFromQuery(token)).toBe(token);
    expect(tokenFromQuery([token, "x"])).toBe(token);
    for (const bad of [undefined, "", "short", "has space ".repeat(5), "<script>".repeat(6), "A".repeat(200)]) expect(tokenFromQuery(bad)).toBeNull();
  });
});
