/** WF-13A-01 and its states, as a person meets them: nothing preselected, an honest country state, one generic answer, no SSO or
 * extra fields, and a link that cannot be used says only that it can't. */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/features/enroll/enroll", () => ({ Enroll: ({ ticket }: { ticket?: string }) => <div data-testid="enroll">{ticket ? "passkey step" : "none"}</div> }));

import { SignUp, SsoOptions } from "./sign-up";
import { SignUpStatus } from "./status";
import { Verify } from "./verify";

const calls: { url: string; init?: RequestInit }[] = [];
let replies: (url: string, init?: RequestInit) => { status: number; body: unknown };

function wrap(node: ReactNode) {
  return <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>{node}</QueryClientProvider>;
}

const capsFor = (country: object | null, providers: string[] = []) => ({
  notice_version: "2026-10-22",
  locale: "und",
  nonce: "nonce-1",
  journeys: ["PERSONAL", "ORGANIZATION"],
  identity_providers: providers,
  authenticators: { primary: "PASSKEY", fallback: "GOVERNED_RECOVERY" },
  country,
});

beforeEach(() => {
  calls.length = 0;
  replies = (url) => {
    if (url.includes("/capabilities")) {
      const code = new URL(url, "http://x").searchParams.get("country");
      if (code === "BR")
        return { status: 200, body: capsFor({ code, account: "NOT_AVAILABLE_YET", routing: { state: "UNAVAILABLE", url: null }, organization_claims: "NOT_AVAILABLE_YET", message: "Zoiko iD accounts are not available for this country yet. Nothing has been stored." }) };
      if (code) return { status: 200, body: capsFor({ code, account: "AVAILABLE", routing: { state: "HERE", url: null }, organization_claims: "AVAILABLE", message: null }) };
      return { status: 200, body: capsFor(null) };
    }
    if (url.includes("/intents")) return { status: 202, body: { request_id: "R".repeat(24), status: "VERIFICATION_REQUESTED", message: "If this address can be used, check your inbox for next steps." } };
    if (url.includes("/status")) return { status: 200, body: { request_id: "R".repeat(24), state: "PENDING_VERIFICATION", next_step: "VERIFY_EMAIL", account: "NONE", identity: "NOT_VERIFIED", organization: "NONE", message: "If this address can be used, check your inbox for next steps.", expires_at: "2026-11-03T00:00:00Z" } };
    return { status: 404, body: {} };
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url, init });
      const r = replies(url, init);
      return { ok: r.status < 400, status: r.status, json: async () => r.body } as Response;
    }),
  );
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("the sign-up form", () => {
  it("opens with the document's heading, nothing selected, and no extra personal fields", async () => {
    render(wrap(<SignUp />));
    expect(screen.getByRole("heading", { name: "Create your Zoiko iD." })).toBeTruthy();
    expect(screen.getByText("Your secure starting point for digital trust.")).toBeTruthy();
    expect(screen.getAllByRole("radio").map((r) => r.closest("label")?.textContent ?? "")).toEqual([
      expect.stringContaining("For myself"),
      expect.stringContaining("For an organization"),
    ]);
    expect(screen.getAllByRole("radio").every((r) => !(r as HTMLInputElement).checked)).toBe(true);
    expect((screen.getByLabelText(/Country or region where you live/) as HTMLSelectElement).value).toBe(""); // no default country
    const email = screen.getByLabelText(/Email address/) as HTMLInputElement;
    expect(email.getAttribute("autocomplete")).toBe("email");
    const text = document.body.textContent?.toLowerCase() ?? "";
    for (const word of ["selfie", "passport", "phone", "card number", "proof of address", "marketing", "newsletter"]) expect(text).not.toContain(word);
    expect(document.querySelectorAll("input[type=checkbox]").length).toBe(0); // no prechecked or bundled consent
    expect(screen.getByRole("link", { name: "Already have a Zoiko iD? Sign in" }).getAttribute("href")).toBe("/sign-in");
    expect(screen.getByRole("link", { name: "Privacy Notice" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "Terms of Service" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Continue securely" })).toBeTruthy();
    await waitFor(() => expect(calls.some((c) => c.url.startsWith("/api/registration/capabilities"))).toBe(true));
  });

  it("says so plainly and refuses to continue for a country that isn't available", async () => {
    const user = userEvent.setup();
    render(wrap(<SignUp />));
    await user.selectOptions(screen.getByLabelText(/Country or region where you live/), "BR");
    expect(await screen.findByText("Not available yet")).toBeTruthy();
    expect(screen.getByText(/Nothing has been stored/)).toBeTruthy();
    expect((screen.getByRole("button", { name: "Continue securely" }) as HTMLButtonElement).disabled).toBe(true);
    expect(calls.some((c) => c.url.includes("/intents"))).toBe(false);
  });

  it("announces what is missing in an alert and sends nothing", async () => {
    const user = userEvent.setup();
    render(wrap(<SignUp />));
    await waitFor(() => expect((screen.getByRole("button", { name: "Continue securely" }) as HTMLButtonElement).disabled).toBe(false));
    await user.click(screen.getByRole("button", { name: "Continue securely" }));
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toMatch(/Choose For myself or For an organization/);
    expect(alert.textContent).toMatch(/Choose the country you live in/);
    expect(alert.textContent).toMatch(/Enter a valid email address/);
    expect(calls.some((c) => c.url.includes("/intents"))).toBe(false);
  });

  it("shows only the generic answer after submitting, and sends the server-issued nonce, notice version and an idempotency key", async () => {
    const user = userEvent.setup();
    render(wrap(<SignUp />));
    await user.click(screen.getByRole("radio", { name: /For myself/ }));
    await user.selectOptions(screen.getByLabelText(/Country or region where you live/), "GB");
    await waitFor(() => expect(screen.queryByText("Not available yet")).toBeNull());
    await user.type(screen.getByLabelText(/Email address/), "jane@example.com");
    await waitFor(() => expect((screen.getByRole("button", { name: "Continue securely" }) as HTMLButtonElement).disabled).toBe(false));
    await user.click(screen.getByRole("button", { name: "Continue securely" }));
    expect(await screen.findByRole("heading", { name: "Check your email" })).toBeTruthy();
    expect(screen.getAllByText("If this address can be used, check your inbox for next steps.").length).toBeGreaterThan(0);
    const sent = calls.find((c) => c.url.includes("/intents"))!;
    expect(JSON.parse(String(sent.init?.body))).toMatchObject({ country: "GB", journey: "PERSONAL", email: "jane@example.com", notice_version: "2026-10-22", nonce: "nonce-1" });
    expect((sent.init?.headers as Record<string, string>)["idempotency-key"]).toMatch(/[0-9a-f-]{36}/);
    expect(JSON.stringify(sent.init?.body)).not.toMatch(/tenant|role|assurance|home_cell/); // nothing the server derives is sent
    expect(screen.getByRole("button", { name: "Use a different email address" })).toBeTruthy();
  });

  it("keeps what was typed when the server refuses recoverably", async () => {
    replies = (url) =>
      url.includes("/intents")
        ? { status: 422, body: { error: { code: "NOTICE_VERSION_STALE" } } }
        : url.includes("/capabilities")
          ? { status: 200, body: capsFor({ code: "GB", account: "AVAILABLE", routing: { state: "HERE", url: null }, organization_claims: "AVAILABLE", message: null }) }
          : { status: 404, body: {} };
    const user = userEvent.setup();
    render(wrap(<SignUp />));
    await user.click(screen.getByRole("radio", { name: /For an organization/ }));
    await user.selectOptions(screen.getByLabelText(/Country or region where you live/), "GB");
    await user.type(screen.getByLabelText(/Email address/), "kept@example.com");
    await waitFor(() => expect((screen.getByRole("button", { name: "Continue securely" }) as HTMLButtonElement).disabled).toBe(false));
    await user.click(screen.getByRole("button", { name: "Continue securely" }));
    expect((await screen.findByRole("alert")).textContent).toMatch(/privacy notice was updated/);
    expect((screen.getByLabelText(/Email address/) as HTMLInputElement).value).toBe("kept@example.com");
    expect((screen.getByLabelText(/Country or region where you live/) as HTMLSelectElement).value).toBe("GB");
    expect((screen.getByRole("radio", { name: /For an organization/ }) as HTMLInputElement).checked).toBe(true);
  });
});

describe("single sign-on", () => {
  it("renders nothing unless the server lists an approved provider", () => {
    const { container } = render(<SsoOptions providers={[]} />);
    expect(container.innerHTML).toBe("");
    cleanup();
    render(<SsoOptions providers={["microsoft"]} />);
    expect(screen.getByRole("link", { name: "Continue with Microsoft" })).toBeTruthy();
  });
});

describe("the emailed link", () => {
  it("shows the document's exact message for a link that fails, whatever the reason", async () => {
    replies = () => ({ status: 410, body: { error: { code: "LINK_INVALID", message: "This link is no longer valid. Request a new link." } } });
    render(wrap(<Verify token={"T".repeat(43)} held={null} />));
    expect(await screen.findByText("This link is no longer valid. Request a new link.")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Request a new link" }).getAttribute("href")).toBe("/sign-up");
    const sent = calls.find((c) => c.url.includes("/confirm"))!;
    expect(JSON.parse(String(sent.init?.body))).toEqual({ token: "T".repeat(43) }); // the link's token alone: no request id, no email
  });

  it("goes to the passkey step with the masked address and the account facts kept apart", () => {
    render(wrap(<Verify token={null} held={{ ticket: "ticket-1", address: "j***@example.com" }} />));
    expect(screen.getByText("j***@example.com")).toBeTruthy();
    expect(screen.getByText("Account active")).toBeTruthy();
    expect(screen.getByText("Identity not verified")).toBeTruthy();
    expect(screen.getByTestId("enroll").textContent).toBe("passkey step");
    expect(screen.getByText(/governed recovery path/)).toBeTruthy();
    expect(document.body.textContent).toMatch(/never accept email or SMS codes/);
  });

  it("asks for the link when there is nothing to continue", async () => {
    replies = () => ({ status: 404, body: {} });
    render(wrap(<Verify token={null} held={null} />));
    expect(screen.getByRole("heading", { name: "Verify your email" })).toBeTruthy();
    expect(await screen.findByText("Open the link in your email")).toBeTruthy();
  });
});

describe("the status view", () => {
  it("offers a resend that always shows the same generic message", async () => {
    const user = userEvent.setup();
    render(wrap(<SignUpStatus />));
    expect(await screen.findByText("Check your inbox")).toBeTruthy();
    expect(screen.getByText("No account yet")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Send a new link" }));
    expect(await screen.findByText("If this address can be used, check your inbox for next steps.", { selector: "p" })).toBeTruthy();
    const resend = calls.find((c) => c.url.includes("/resend"))!;
    expect(JSON.parse(String(resend.init?.body))).toEqual({}); // the browser's own request is found server-side; no id is sent
  });

  it("links an organization journey to the existing claim screen and never calls the identity verified", async () => {
    replies = () => ({ status: 200, body: { request_id: "R".repeat(24), state: "COMPLETED", next_step: "ORGANIZATION_CLAIM", account: "ACTIVE", identity: "NOT_VERIFIED", organization: "NONE", message: null, expires_at: "2026-11-03T00:00:00Z" } });
    render(wrap(<SignUpStatus />));
    expect((await screen.findByRole("link", { name: "Find your organization" })).getAttribute("href")).toBe("/admin/organization/claim");
    expect(screen.getByText("Account active")).toBeTruthy();
    expect(screen.getByText("Identity not verified")).toBeTruthy();
    expect(document.body.textContent?.toLowerCase()).not.toMatch(/identity verified|verified company|you are an owner/);
  });

  it("uses the exact held copy and reveals no score", async () => {
    replies = () => ({ status: 200, body: { request_id: "R".repeat(24), state: "ON_HOLD", next_step: "CONTACT_SUPPORT", account: "NONE", identity: "NOT_VERIFIED", organization: "NONE", message: "We couldn't complete registration securely. Contact support or try again later.", expires_at: "2026-11-03T00:00:00Z" } });
    render(wrap(<SignUpStatus />));
    expect(await screen.findByText("We couldn't complete registration securely. Contact support or try again later.")).toBeTruthy();
    expect(document.body.textContent?.toLowerCase()).not.toMatch(/score|fraud|risk/);
  });
});
