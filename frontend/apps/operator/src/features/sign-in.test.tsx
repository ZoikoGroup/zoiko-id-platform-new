/** The operator sign-in page: operator accounts only, and a one-way pointer to the company site for a customer who opened it by mistake. */
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/runtime", () => ({ useRuntime: () => ({ flow: { operatorPasswordSignIn: vi.fn() } }) }));
vi.mock("@/lib/webauthn", () => ({ completeAppSession: vi.fn(), passkeyErrorMessage: (e: unknown) => String(e) }));

import { OperatorSignIn, companySignInHref } from "./sign-in";

afterEach(cleanup);

describe("company sign-in pointer", () => {
  it("accepts only a plain http or https address", () => {
    expect(companySignInHref("https://id.example.com/sign-in")).toBe("https://id.example.com/sign-in");
    expect(companySignInHref("http://localhost:3000/sign-in")).toBe("http://localhost:3000/sign-in");
    expect(companySignInHref("javascript:alert(1)")).toBeNull();
    expect(companySignInHref("data:text/html,x")).toBeNull();
    expect(companySignInHref("not a url")).toBeNull();
    expect(companySignInHref("")).toBeNull();
    expect(companySignInHref(undefined)).toBeNull();
  });

  it("shows the link when an address is configured", () => {
    render(<OperatorSignIn companyUrl="https://id.example.com/sign-in" />);
    const link = screen.getByRole("link", { name: "Sign in on the company site" });
    expect(link.getAttribute("href")).toBe("https://id.example.com/sign-in");
  });

  it("shows no link when none is configured or the address is unsafe", () => {
    render(<OperatorSignIn />);
    expect(screen.queryByRole("link")).toBeNull();
    cleanup();
    render(<OperatorSignIn companyUrl="javascript:alert(1)" />);
    expect(screen.queryByRole("link")).toBeNull();
  });

  it("still says it is for operator accounts only, with a password form and no passkey option", () => {
    render(<OperatorSignIn />);
    expect(screen.getByRole("heading", { name: "Operator sign-in" })).toBeTruthy();
    expect(screen.getByText(/Customer accounts cannot sign in here/)).toBeTruthy();
    expect(screen.getByLabelText(/Password/)).toBeTruthy();
    expect(screen.queryByText(/passkey/i)).toBeNull();
  });
});
