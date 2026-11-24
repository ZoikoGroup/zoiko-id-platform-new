import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AssistantWidget } from "../src";
import { baseRoutes, BASE, BOOT, err, mockApi, packet } from "./helpers";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

async function openAndAsk(routes: ReturnType<typeof baseRoutes>, text = "What is Zoiko iD?") {
  const api = mockApi(routes);
  const user = userEvent.setup();
  render(<AssistantWidget apiBase={BASE} fetchImpl={api.fetchImpl} fallbackLinks={[{ label: "Home", href: "/" }]} />);
  await user.click(screen.getByRole("button", { name: /open the zoiko id assistant/i }));
  const box = await screen.findByLabelText(/ask a question/i);
  await user.type(box, text);
  await user.click(screen.getByRole("button", { name: /^send$/i }));
  return { api, user };
}

describe("answers are text, links are server-issued", () => {
  it("renders an XSS payload and Markdown as plain text", async () => {
    const evil = `<img src=x onerror="window.__zpa=1"><script>window.__zpa=2</script> **bold** [x](javascript:alert(1))`;
    await openAndAsk(baseRoutes({ "POST /messages": { status: 200, body: packet({ answer_text: evil }) } }));
    const card = await screen.findByRole("article");
    expect(card.textContent).toContain(evil); // shown literally
    expect(card.querySelector("img, script, strong, a[href^='javascript']")).toBeNull();
    expect((window as unknown as { __zpa?: number }).__zpa).toBeUndefined();
  });

  it("opens links only through /out/{link_id}; ignores unknown actions and URLs in text", async () => {
    const p = packet({
      answer_text: "See https://evil.test/phish for more.",
      citations: [{ source_id: "PUB-001", revision: "r0", title: "Overview page", link_id: "OVERVIEW" }, { source_id: "PUB-002", revision: "r0", title: "No link" }],
      actions: [
        { type: "OPEN_APPROVED_LINK", link_id: "DEV-DOCS", label: "Developer docs" },
        { type: "OPEN_APPROVED_LINK", label: "No id" },
        { type: "RUN_SCRIPT", link_id: "X" },
      ],
    });
    await openAndAsk(baseRoutes({ "POST /messages": { status: 200, body: p } }));
    const card = await screen.findByRole("article");
    const anchors = within(card).getAllByRole("link");
    // order is a layout choice (the action link sits above the sources); what matters is that every link is a server-issued id
    expect(anchors.map((a) => a.getAttribute("href")).sort()).toEqual([`${BASE}/out/DEV-DOCS`, `${BASE}/out/OVERVIEW`]);
    for (const a of anchors) expect(a.getAttribute("rel")).toContain("noopener");
    expect(within(card).queryByText("No id")).toBeNull();
    expect(card.textContent).toContain("https://evil.test/phish"); // text, not a link
  });

  it("labels sample content", async () => {
    await openAndAsk(baseRoutes({ "POST /messages": { status: 200, body: packet() } }));
    await screen.findByRole("article");
    expect(screen.getAllByText(/engineering sample content/i).length).toBeGreaterThan(1); // banner + card
  });
});

describe("failures never become answers", () => {
  it("503 on send shows the unavailable fallback and help links, not an answer", async () => {
    await openAndAsk(baseRoutes({ "POST /messages": err(503, "UNAVAILABLE") }));
    expect(await screen.findByText(/assistant is unavailable/i)).toBeTruthy();
    expect(screen.queryByRole("article")).toBeNull();
    expect(screen.getAllByRole("link", { name: /platform overview/i }).length).toBeGreaterThan(0);
  });

  it("a network failure behaves the same", async () => {
    const api = mockApi(baseRoutes());
    const failing = vi.fn(async (u: RequestInfo | URL, i?: RequestInit) => {
      if (String(u).endsWith("/messages")) throw new TypeError("offline");
      return api.fetchImpl(u, i);
    }) as unknown as typeof fetch;
    const user = userEvent.setup();
    render(<AssistantWidget apiBase={BASE} fetchImpl={failing} />);
    await user.click(screen.getByRole("button", { name: /open the zoiko id assistant/i }));
    await user.type(await screen.findByLabelText(/ask a question/i), "hello");
    await user.click(screen.getByRole("button", { name: /^send$/i }));
    expect(await screen.findByText(/assistant is unavailable/i)).toBeTruthy();
    expect(screen.queryByRole("article")).toBeNull();
  });

  it("a failed bootstrap shows host fallback links and a retry", async () => {
    const api = mockApi({ "GET /bootstrap": err(503, "UNAVAILABLE") });
    const user = userEvent.setup();
    render(<AssistantWidget apiBase={BASE} fetchImpl={api.fetchImpl} fallbackLinks={[{ label: "Home page", href: "/" }]} />);
    await user.click(screen.getByRole("button", { name: /open the zoiko id assistant/i }));
    expect(await screen.findByRole("link", { name: "Home page" })).toBeTruthy();
    expect(screen.getByRole("button", { name: /try again/i })).toBeTruthy();
    expect(screen.queryByLabelText(/ask a question/i)).toBeNull();
  });

  it("a secret-blocked message (422) shows the safety copy and drops the text", async () => {
    const secret = "my password is hunter2-REALLY-SECRET";
    await openAndAsk(baseRoutes({ "POST /messages": err(422, "SENSITIVE_CONTENT_BLOCKED") }), secret);
    expect(await screen.findByText(/was not sent or stored/i)).toBeTruthy();
    expect(screen.getByText(/message not sent/i)).toBeTruthy();
    expect(document.body.textContent).not.toContain("hunter2");
  });

  it("a 429 tells the person how long to wait", async () => {
    await openAndAsk(baseRoutes({ "POST /messages": err(429, "RESOURCE_LIMIT", { retry_after_s: 42 }) }));
    expect(await screen.findByText(/about 42 seconds/i)).toBeTruthy();
  });

  it("recreates an expired session once and replays the same message id", async () => {
    let n = 0;
    const ids: string[] = [];
    const api = mockApi(
      baseRoutes({
        "POST /messages": ({ body }) => {
          ids.push((body as { client_message_id: string }).client_message_id);
          return ++n === 1 ? err(401, "INVALID_GUEST_SESSION") : { status: 200, body: packet() };
        },
      }),
    );
    const user = userEvent.setup();
    render(<AssistantWidget apiBase={BASE} fetchImpl={api.fetchImpl} />);
    await user.click(screen.getByRole("button", { name: /open the zoiko id assistant/i }));
    await user.type(await screen.findByLabelText(/ask a question/i), "hi");
    await user.click(screen.getByRole("button", { name: /^send$/i }));
    expect(await screen.findByRole("article")).toBeTruthy();
    expect(ids.length).toBe(2);
    expect(ids[0]).toBe(ids[1]);
    expect(api.calls.filter((c) => c.path === "/sessions" && c.method === "POST").length).toBe(2);
  });
});

describe("disabled mode", () => {
  it("shows only the static help links when the service is switched off", async () => {
    const api = mockApi({ "GET /bootstrap": { status: 200, body: { ...BOOT, enabled: false } } });
    const user = userEvent.setup();
    render(<AssistantWidget apiBase={BASE} fetchImpl={api.fetchImpl} />);
    await user.click(screen.getByRole("button", { name: /open the zoiko id assistant/i }));
    expect(await screen.findByText(/switched off/i)).toBeTruthy();
    expect(screen.getByRole("link", { name: /platform overview/i }).getAttribute("href")).toBe(`${BASE}/out/OVERVIEW`);
    expect(screen.queryByLabelText(/ask a question/i)).toBeNull();
    expect(screen.queryByRole("button", { name: /^send$/i })).toBeNull();
  });
});

describe("focus and keyboard", () => {
  it("moves focus into the panel, traps Tab, closes on Escape and returns focus to the launcher", async () => {
    const api = mockApi(baseRoutes());
    const user = userEvent.setup();
    render(<AssistantWidget apiBase={BASE} fetchImpl={api.fetchImpl} />);
    const launcher = screen.getByRole("button", { name: /open the zoiko id assistant/i });
    await user.click(launcher);
    const dialog = await screen.findByRole("dialog");
    await screen.findByLabelText(/ask a question/i);
    await waitFor(() => expect(dialog.contains(document.activeElement)).toBe(true));

    const focusables = Array.from(dialog.querySelectorAll<HTMLElement>("a[href], button:not([disabled]), textarea:not([disabled])"));
    const first = focusables[0]!;
    const last = focusables[focusables.length - 1]!;
    last.focus();
    fireEvent.keyDown(last, { key: "Tab" });
    expect(document.activeElement).toBe(first); // wraps forward
    fireEvent.keyDown(first, { key: "Tab", shiftKey: true });
    expect(document.activeElement).toBe(last); // wraps backward

    fireEvent.keyDown(dialog, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(document.activeElement).toBe(launcher);
  });
});

describe("nothing is stored in the browser", () => {
  it("writes no local/session storage and sets no cookie during a full conversation", async () => {
    const local = vi.spyOn(Storage.prototype, "setItem");
    const cookie = vi.spyOn(document, "cookie", "set");
    await openAndAsk(baseRoutes({ "POST /messages": { status: 200, body: packet() } }));
    await screen.findByRole("article");
    expect(local).not.toHaveBeenCalled();
    expect(cookie).not.toHaveBeenCalled();
    expect(window.localStorage.length + window.sessionStorage.length).toBe(0);
  });
});

describe("forget this chat", () => {
  it("deletes the server session and clears the transcript", async () => {
    const { api, user } = await openAndAsk(baseRoutes({ "POST /messages": { status: 200, body: packet() } }));
    await screen.findByRole("article");
    await user.click(screen.getByRole("button", { name: /forget this chat/i }));
    await waitFor(() => expect(api.calls.some((c) => c.method === "DELETE" && c.path === "/sessions")).toBe(true));
    expect(screen.queryByRole("article")).toBeNull();
    expect(await screen.findByText(/chat was cleared/i)).toBeTruthy();
  });
});

describe("feedback", () => {
  it("sends only a rating for the response id", async () => {
    const fb: unknown[] = [];
    const { user } = await openAndAsk(
      baseRoutes({ "POST /messages": { status: 200, body: packet() }, "POST /feedback": ({ body }) => (fb.push(body), { status: 204 }) }),
    );
    await screen.findByRole("article");
    await user.click(screen.getByRole("button", { name: /helpful: this answer helped/i }));
    await waitFor(() => expect(fb).toEqual([{ response_id: "rsp_1", rating: "USEFUL" }]));
  });
});

describe("a host can reword the widget without changing what it does", () => {
  const COPY = { launcher: "Open the console guide", launcherClose: "Close the console guide", inputLabel: "Ask about your role", welcomeBody: "I explain roles." };

  it("uses the host's wording where given and the defaults everywhere else", async () => {
    const api = mockApi(baseRoutes());
    const user = userEvent.setup();
    render(<AssistantWidget apiBase={BASE} fetchImpl={api.fetchImpl} copy={COPY} />);
    expect(screen.queryByRole("button", { name: /open the zoiko id assistant/i })).toBeNull();
    await user.click(screen.getByRole("button", { name: /open the console guide/i }));
    expect(await screen.findByLabelText(/ask about your role/i)).toBeTruthy();
    expect(screen.getByText("I explain roles.")).toBeTruthy();
    expect(screen.getByRole("button", { name: /^send$/i })).toBeTruthy(); // not overridden: default stays
    expect(screen.getByRole("button", { name: /close the console guide/i })).toBeTruthy();
  });

  it("without overrides the public wording is unchanged", async () => {
    const api = mockApi(baseRoutes());
    render(<AssistantWidget apiBase={BASE} fetchImpl={api.fetchImpl} />);
    expect(screen.getByRole("button", { name: /open the zoiko id assistant/i })).toBeTruthy();
  });
});

describe("size toggle (website)", () => {
  it("is absent by default and enlarges / restores the panel when resizable", async () => {
    const api = mockApi(baseRoutes());
    const { unmount } = render(<AssistantWidget apiBase={BASE} fetchImpl={api.fetchImpl} defaultOpen />);
    await screen.findByRole("dialog");
    expect(screen.queryByRole("button", { name: /larger/i })).toBeNull();
    unmount();
    render(<AssistantWidget apiBase={BASE} fetchImpl={mockApi(baseRoutes()).fetchImpl} defaultOpen resizable />);
    const panel = await screen.findByRole("dialog");
    expect(panel.className).not.toContain("zpa-panel-max");
    await userEvent.click(screen.getByRole("button", { name: /larger/i }));
    expect(panel.className).toContain("zpa-panel-max");
    await userEvent.click(screen.getByRole("button", { name: /smaller/i }));
    expect(panel.className).not.toContain("zpa-panel-max");
  });
});
