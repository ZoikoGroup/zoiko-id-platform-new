/** The hosted verification rendered against a mocked relay (WF-01 S01, S02, S10; Q27): the link is spent once and dropped from the address bar, the notice
 * screen asks for consent before anything else, only the server's methods are offered, and each screen passes axe. Real devices and screen readers stay a
 * manual pass. */
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import axe from "axe-core";
import { afterEach, describe, expect, it, vi } from "vitest";

import { HostedFlow } from "./hosted-flow";

if (!(globalThis as { CSS?: unknown }).CSS) Object.defineProperty(globalThis, "CSS", { value: { escape: (v: string) => v.replace(/[^\w-]/g, (c) => `\\${c}`) } });

const REF = "vs_0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0";
const TOKEN = `0f1e2d3c4b5a69788796a5b4c3d2e1f0.${"a".repeat(43)}`;
const status = (over: Record<string, unknown> = {}) => ({
  session_ref: REF, reference: "ZID-0F1E2D3C4B", state: "NOTICE_PRESENTED", acknowledged: false, biometric_consent: false, method: null,
  submitted: false, final: false, action: null, expires_at: null, mode: "sandbox", ...over,
}); // prettier-ignore
const context = {
  requester: { name: "Acme Bank", verified_by: "Zoiko iD" },
  purpose_code: "ACCOUNT_OPENING",
  market: "GB",
  notice: { notice_version: "N@1", content_uri: "https://acme.example/notice", acknowledged: false },
  market_notice_version: "gb-1",
  lawful_basis: "CONSENT",
  languages: ["en"],
  methods: [
    { method: "CHIP", kind: "CHIP_AND_DOCUMENT", needs_nfc: true, needs_camera: true, biometric: false, requires_handoff: true },
    { method: "WEB_AUTO", kind: "DOCUMENT_AUTOMATIC", needs_nfc: false, needs_camera: true, biometric: false, requires_handoff: false },
  ],
  methods_withheld: [{ method: "WEB_FACE", reason: "BIOMETRICS_NOT_AUTHORIZED_IN_MARKET" }],
  status: status(),
};

function reply(body: unknown, code = 200) {
  return Promise.resolve(new Response(JSON.stringify(body), { status: code, headers: { "content-type": "application/json" } }));
}

async function clean(container: HTMLElement) {
  const result = await axe.run(container, { rules: { "color-contrast": { enabled: false }, region: { enabled: false } } });
  expect(result.violations.map((v) => v.id)).toEqual([]);
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("HostedFlow", () => {
  it("spends the link once, asks for consent first, and offers only the server's methods", async () => {
    const calls: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string, init?: RequestInit) => {
        calls.push(`${init?.method ?? "GET"} ${url}`);
        if (url.endsWith("/launch")) return reply({ session_ref: REF, access_token: TOKEN, expires_at: "2999-01-01T00:00:00Z" });
        if (url.includes("/public-context")) return reply(context);
        if (url.endsWith("/notices")) return reply(status({ acknowledged: true }));
        if (url.endsWith("/handoff")) return reply({ handoff_url: `https://id.example/verify-session#t=${TOKEN}`, expires_at: new Date(Date.now() + 5 * 60000).toISOString() });
        return reply({}, 404);
      }),
    );
    window.history.replaceState(null, "", `/verify-session#t=${TOKEN}`);
    const { container } = render(<HostedFlow />);

    expect(await screen.findByRole("heading", { name: "Why we need to verify your identity" })).toBeTruthy();
    expect(window.location.hash).toBe(""); // the token never stays in the address bar
    expect(calls.filter((c) => c.endsWith("/launch"))).toHaveLength(1);
    expect(screen.getByText(/Acme Bank · Secured by Zoiko iD/)).toBeTruthy();
    const section = container.querySelector("section[aria-labelledby='flow-h']");
    expect(section?.getAttribute("lang")).toBe("en");
    expect(section?.getAttribute("dir")).toBe("ltr");
    await clean(container);

    const go = screen.getByRole("button", { name: "Continue" });
    expect(go.hasAttribute("disabled") || go.getAttribute("aria-disabled") === "true" || go.hasAttribute("data-disabled")).toBe(true); // consent basis: nothing before agreement
    await userEvent.click(screen.getByText("I agree to this verification for the purpose above."));
    await userEvent.click(screen.getByRole("button", { name: "Continue" }));

    expect(await screen.findByRole("heading", { name: "Choose how to confirm your identity" })).toBeTruthy();
    expect(screen.getByText("Photograph of your ID")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Get a link for my phone" })).toBeTruthy(); // the chip route needs a phone: a handoff, not a fake read
    expect(screen.getByText("A face comparison is not offered where you are.")).toBeTruthy();
    await clean(container);

    // S06: the one-use link for another device, as a scannable code with its real expiry
    await userEvent.click(screen.getByRole("button", { name: "Get a link for my phone" }));
    expect(await screen.findByRole("img", { name: "Code to continue on your phone" })).toBeTruthy();
    expect(screen.getByText(/within 5 minutes/)).toBeTruthy();
    await clean(container);
  });

  it("tells the person plainly when the link cannot be used", async () => {
    vi.stubGlobal("fetch", vi.fn(() => reply({ error: { code: "TOKEN_ALREADY_USED", message: "the link was already used" } }, 401)));
    window.history.replaceState(null, "", `/verify-session#t=${TOKEN}`);
    render(<HostedFlow />);
    await waitFor(() => expect(screen.getByRole("heading", { name: "This link cannot be used" })).toBeTruthy());
  });
});
