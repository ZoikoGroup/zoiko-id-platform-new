import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";

import { AssistantWidget } from "../src";
import { baseRoutes, BASE, mockApi, packet } from "./helpers";

afterEach(cleanup);

const SALES_CFG = {
  available: true,
  form_revision: "intake-sales-v1",
  notice_id: "n1",
  notice_text: "We use these details only to answer this request.",
  fields: [
    { name: "email", type: "email", required: true, max_length: 254 },
    { name: "country_code", type: "country", required: true, max_length: 2 },
    { name: "inquiry_type", type: "select", required: true, options: ["DEMO", "SALES"] },
  ],
};

async function open(routes = baseRoutes({ "POST /messages": { status: 200, body: packet() } }), fetchImpl?: typeof fetch) {
  const api = mockApi(routes);
  const user = userEvent.setup();
  render(<AssistantWidget apiBase={BASE} fetchImpl={fetchImpl ?? api.fetchImpl} />);
  await user.click(screen.getByRole("button", { name: /open the zoiko id assistant/i }));
  return { api, user };
}

async function ask(user: ReturnType<typeof userEvent.setup>, text: string) {
  await user.type(await screen.findByLabelText(/ask a question/i), text);
  await user.click(screen.getByRole("button", { name: /^send$/i }));
}

describe("features carried over from the website assistant", () => {
  it("switches between light and dark, in memory only", async () => {
    const { user } = await open();
    const toggle = await screen.findByRole("button", { name: /switch to dark mode/i });
    expect(toggle.getAttribute("aria-pressed")).toBe("false");
    await user.click(toggle);
    expect(document.querySelector(".zpa-root")?.classList.contains("zpa-dark")).toBe(true);
    await user.click(screen.getByRole("button", { name: /switch to light mode/i }));
    expect(document.querySelector(".zpa-root")?.classList.contains("zpa-dark")).toBe(false);
  });

  it("cancels a question that is still being answered and says nothing more was sent", async () => {
    const api = mockApi(baseRoutes({ "POST /messages": { status: 200, body: packet() } }));
    // a fetch that never answers on its own and honours the abort signal
    const hanging = ((input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).endsWith("/messages")) {
        return new Promise<Response>((_res, rej) => {
          init?.signal?.addEventListener("abort", () => rej(new DOMException("aborted", "AbortError")));
        });
      }
      return api.fetchImpl(input, init);
    }) as typeof fetch;
    const { user } = await open(undefined, hanging);
    await ask(user, "a slow question");
    await user.click(await screen.findByRole("button", { name: /^cancel$/i }));
    expect(await screen.findByText(/cancelled\. nothing more was sent/i)).toBeTruthy();
    expect(screen.queryByRole("button", { name: /^cancel$/i })).toBeNull(); // the pending row is gone
  });

  it("deletes one earlier chat from this window's history", async () => {
    const { user } = await open();
    await ask(user, "first question");
    await screen.findByRole("article");
    await user.click(screen.getByRole("button", { name: "New chat" }));
    await user.click(screen.getByRole("button", { name: "History" }));
    const del = await screen.findByRole("button", { name: /delete this chat: first question/i });
    await user.click(del);
    expect(await screen.findByText(/no earlier chats/i)).toBeTruthy();
  });

  it("says so when an answer carries no approved source", async () => {
    const { user } = await open();
    await ask(user, "anything");
    expect(await screen.findByText(/not verified against an approved source/i)).toBeTruthy();
  });

  it("does not show that label when the answer cites a source", async () => {
    const cited = packet({ citations: [{ source_id: "PUB-001", revision: "r0", title: "Identity concepts", span_id: "s1", link_id: null, reviewed_at: "2026-10-01" }] });
    const { user } = await open(baseRoutes({ "POST /messages": { status: 200, body: cited } }));
    await ask(user, "anything");
    await screen.findByRole("article");
    expect(screen.queryByText(/not verified against an approved source/i)).toBeNull();
    expect(screen.getByText(/r0, reviewed/i)).toBeTruthy();
  });
});

describe("intake form additions", () => {
  async function openForm() {
    const api = mockApi(
      baseRoutes({
        "POST /handoffs": { status: 200, body: SALES_CFG },
        "GET /intake/config": { status: 200, body: { sales: SALES_CFG, support: { available: false }, marketing: { available: false } } },
        "POST /intake/sales": { status: 202, body: { reference: "ZR-1", status: "ACCEPTED" } },
      }),
    );
    const user = userEvent.setup();
    render(<AssistantWidget apiBase={BASE} fetchImpl={api.fetchImpl} />);
    await user.click(screen.getByRole("button", { name: /open the zoiko id assistant/i }));
    await user.click(await screen.findByRole("button", { name: /talk to sales/i }));
    await screen.findByRole("heading", { name: /talk to sales/i, level: 3 });
    return { api, user };
  }

  it("offers countries as a list and sends the chosen code", async () => {
    const { api, user } = await openForm();
    const select = screen.getByLabelText(/^country/i);
    expect(select.tagName).toBe("SELECT");
    expect(within(select).getByRole("option", { name: "India (IN)" })).toBeTruthy();
    await user.type(screen.getByLabelText(/email address|^email/i), "buyer@example.test");
    await user.selectOptions(select, "IN");
    await user.selectOptions(screen.getByLabelText(/what is this about|inquiry type/i), "DEMO");
    await user.click(screen.getByLabelText(/i have read the notice/i));
    await user.click(screen.getByRole("button", { name: /send request/i }));
    await waitFor(() => expect(api.calls.some((c) => c.path === "/intake/sales")).toBe(true));
    expect(api.calls.find((c) => c.path === "/intake/sales")?.body).toMatchObject({ country_code: "IN" });
  });

  it("sends nothing and claims nothing when the hidden spam-trap field is filled", async () => {
    const { api, user } = await openForm();
    await user.type(screen.getByLabelText(/email address|^email/i), "bot@example.test");
    await user.selectOptions(screen.getByLabelText(/^country/i), "US");
    await user.selectOptions(screen.getByLabelText(/what is this about|inquiry type/i), "DEMO");
    await user.type(screen.getByLabelText(/leave this field empty/i), "x");
    await user.click(screen.getByLabelText(/i have read the notice/i));
    await user.click(screen.getByRole("button", { name: /send request/i }));
    expect(api.calls.some((c) => c.path === "/intake/sales")).toBe(false);
    expect(document.body.textContent).not.toContain("ZR-");
  });
});
