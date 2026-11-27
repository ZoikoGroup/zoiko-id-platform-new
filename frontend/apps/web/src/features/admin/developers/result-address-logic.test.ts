import { describe, expect, it } from "vitest";

import { addressProblem, isIdentityEvent } from "./result-address-logic";

describe("the result address", () => {
  it("says nothing until something is typed", () => {
    expect(addressProblem("")).toBeNull();
    expect(addressProblem("   ")).toBeNull();
  });
  it("accepts a plain https address with a path", () => {
    expect(addressProblem("https://rooms.example.com/api/zoiko-id/webhook")).toBeNull();
  });
  it("asks for https", () => {
    expect(addressProblem("http://rooms.example.com/hook")).toMatch(/https/);
    expect(addressProblem("rooms.example.com/hook")).toMatch(/https/);
  });
  it("refuses localhost, an IP address and a custom port, each with its own words", () => {
    expect(addressProblem("https://localhost/hook")).toMatch(/tunnel/);
    expect(addressProblem("https://203.0.113.5/hook")).toMatch(/IP address/);
    expect(addressProblem("https://rooms.example.com:8443/hook")).toMatch(/port/);
  });
  it("refuses a space", () => {
    expect(addressProblem("https://rooms.example.com/ho ok")).toMatch(/space/);
  });
});

describe("the identity events", () => {
  it("matches the two verification events by their short name and nothing else", () => {
    expect(isIdentityEvent("com.zoiko.id.verification.api.session_decided.v1")).toBe(true);
    expect(isIdentityEvent("com.zoiko.id.verification.api.session_closed.v1")).toBe(true);
    expect(isIdentityEvent("com.zoiko.id.mandate.created.v1")).toBe(false);
  });
});
