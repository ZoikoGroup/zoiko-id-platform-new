import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";

import { AssistantWidget } from "../src";
import { baseRoutes, BASE, BOOT, err, mockApi, packet, type Reply } from "./helpers";

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
    { name: "message", type: "textarea", max_length: 500 },
  ],
};

async function openForm(submit: Reply | (() => Promise<Reply>), overrides: Record<string, unknown> = {}) {
  let release: () => void = () => {};
  const gate = new Promise<void>((r) => (release = r));
  const api = mockApi(
    baseRoutes({
      "POST /handoffs": { status: 200, body: SALES_CFG },
      "GET /intake/config": { status: 200, body: { sales: SALES_CFG, support: { available: false }, marketing: { available: false } } },
      "POST /intake/sales": typeof submit === "function" ? async () => submit() : { ...submit, delay: gate },
      ...overrides,
    }),
  );
  const user = userEvent.setup();
  render(<AssistantWidget apiBase={BASE} fetchImpl={api.fetchImpl} />);
  await user.click(screen.getByRole("button", { name: /open the zoiko id assistant/i }));
  await user.click(await screen.findByRole("button", { name: /talk to sales/i }));
  await screen.findByRole("heading", { name: /talk to sales/i, level: 3 });
  return { api, user, release };
}

async function fill(user: ReturnType<typeof userEvent.setup>) {
  await user.type(screen.getByLabelText(/email address|^email/i), "buyer@example.test");
  await user.selectOptions(screen.getByLabelText(/^country/i), "US");
  await user.selectOptions(screen.getByLabelText(/what is this about|inquiry type/i), "DEMO");
}

describe("intake forms (WF-10A)", () => {
  it("shows the reference only after the server accepts (202), never before", async () => {
    const { api, user, release } = await openForm({ status: 202, body: { reference: "ZR-8F3K2M9Q", status: "ACCEPTED" } });
    await fill(user);
    await user.click(screen.getByLabelText(/i have read the notice/i));
    await user.click(screen.getByRole("button", { name: /send request/i }));
    await screen.findByRole("button", { name: /sending/i });
    expect(document.body.textContent).not.toContain("ZR-"); // submitted but not yet durably accepted
    release();
    expect(await screen.findByText("ZR-8F3K2M9Q")).toBeTruthy();
    const call = api.calls.find((c) => c.path === "/intake/sales")!;
    expect(call.headers["idempotency-key"]).toMatch(/[0-9a-f-]{20,}/);
    expect(call.body).toMatchObject({ form_revision: "intake-sales-v1", purpose: "sales_inquiry", country_code: "US", notice_accepted: true });
    expect(JSON.stringify(call.body)).not.toMatch(/transcript|messages/i); // the chat is never attached
  });

  it("a 503 says nothing was submitted and shows no reference", async () => {
    const { user } = await openForm(async () => err(503, "UNAVAILABLE"));
    await fill(user);
    await user.click(screen.getByLabelText(/i have read the notice/i));
    await user.click(screen.getByRole("button", { name: /send request/i }));
    expect(await screen.findByRole("alert")).toBeTruthy();
    expect(screen.getByRole("alert").textContent).toMatch(/nothing was submitted/i);
    expect(document.body.textContent).not.toContain("ZR-");
  });

  it("will not submit without the notice being confirmed or required fields filled", async () => {
    const { api, user } = await openForm({ status: 202, body: { reference: "ZR-1", status: "ACCEPTED" } });
    await user.click(screen.getByRole("button", { name: /send request/i }));
    expect((await screen.findByRole("alert")).textContent).toMatch(/check the marked fields/i);
    await fill(user);
    await user.click(screen.getByRole("button", { name: /send request/i }));
    expect((await screen.findByRole("alert")).textContent).toMatch(/confirm the notice/i);
    expect(api.calls.some((c) => c.path === "/intake/sales")).toBe(false);
  });

  it("keeps marketing hidden unless the flag and policy are live, and unchecked when shown", async () => {
    await openForm({ status: 202, body: { reference: "ZR-1", status: "ACCEPTED" } });
    expect(screen.queryByLabelText(/product news/i)).toBeNull();
    cleanup();
    const api = mockApi(
      baseRoutes({
        "GET /bootstrap": { status: 200, body: { ...BOOT, flags: { ...BOOT.flags, marketing: true } } },
        "POST /handoffs": { status: 200, body: SALES_CFG },
        "GET /intake/config": { status: 200, body: { sales: SALES_CFG, support: { available: false }, marketing: { available: true, notice_id: "m1" } } },
      }),
    );
    const user = userEvent.setup();
    render(<AssistantWidget apiBase={BASE} fetchImpl={api.fetchImpl} />);
    await user.click(screen.getByRole("button", { name: /open the zoiko id assistant/i }));
    await user.click(await screen.findByRole("button", { name: /talk to sales/i }));
    const box = (await screen.findByLabelText(/product news/i)) as HTMLInputElement;
    expect(box.checked).toBe(false);
  });

  it("an unavailable purpose shows no form, only the public pages", async () => {
    const api = mockApi(baseRoutes({ "POST /handoffs": { status: 200, body: { available: false } }, "GET /intake/config": { status: 200, body: { sales: { available: false }, support: { available: false }, marketing: { available: false } } } }));
    const user = userEvent.setup();
    render(<AssistantWidget apiBase={BASE} fetchImpl={api.fetchImpl} />);
    await user.click(screen.getByRole("button", { name: /open the zoiko id assistant/i }));
    await user.click(await screen.findByRole("button", { name: /get support/i }));
    expect(await screen.findByText(/isn't available right now/i)).toBeTruthy();
    expect(screen.queryByRole("button", { name: /send request/i })).toBeNull();
    expect(screen.getAllByRole("link", { name: /platform overview/i }).length).toBeGreaterThan(0);
  });

  it("hides the handoff buttons when the flags are off", async () => {
    const off = { ...BOOT, flags: { ...BOOT.flags, intake_sales: false, intake_support: false } };
    const api = mockApi(baseRoutes({ "GET /bootstrap": { status: 200, body: off } }));
    const user = userEvent.setup();
    render(<AssistantWidget apiBase={BASE} fetchImpl={api.fetchImpl} />);
    await user.click(screen.getByRole("button", { name: /open the zoiko id assistant/i }));
    await screen.findByLabelText(/ask a question/i);
    expect(screen.queryByRole("button", { name: /talk to sales|get support/i })).toBeNull();
  });

  it.each([
    [true, 1],
    [false, 0],
  ])("offers a handoff action from an answer only when its purpose is enabled (support flag %s)", async (flag, expected) => {
    const boot = { ...BOOT, flags: { ...BOOT.flags, intake_sales: false, intake_support: flag } };
    const api = mockApi(
      baseRoutes({
        "GET /bootstrap": { status: 200, body: boot },
        "POST /messages": { status: 200, body: packet({ actions: [{ type: "START_HANDOFF_SUPPORT", label: "Get support" }, { type: "START_HANDOFF_SALES", label: "Talk to sales" }] }) },
      }),
    );
    const user = userEvent.setup();
    render(<AssistantWidget apiBase={BASE} fetchImpl={api.fetchImpl} />);
    await user.click(screen.getByRole("button", { name: /open the zoiko id assistant/i }));
    await user.type(await screen.findByLabelText(/ask a question/i), "help");
    await user.click(screen.getByRole("button", { name: /^send$/i }));
    const card = await screen.findByRole("article");
    await waitFor(() => expect(card).toBeTruthy());
    expect(within(card).queryAllByRole("button", { name: /get support/i }).length).toBe(expected);
    expect(within(card).queryAllByRole("button", { name: /talk to sales/i }).length).toBe(0); // sales flag is off in both cases
  });
});
