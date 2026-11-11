import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AssistantWidget } from "../src";
import { baseRoutes, BASE, BOOT, mockApi, packet } from "./helpers";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const TILES = [
  { label: "Authentication & passkeys", text: "How do passkeys work?", description: "Passwordless, MFA and phishing-resistant access.", icon: "lock" },
  { label: "Verifiable credentials", text: "What are verifiable credentials?", description: "Digital identity, KYC and trusted data sharing.", icon: "document" },
  { label: "Consent & access", text: "How does consent work?", description: "User-controlled data sharing.", icon: "shield" },
  { label: "APIs & integrations", text: "How do I integrate?", description: "Standards, SDKs and enterprise integrations.", icon: "share" },
  { label: "Pricing", text: "How much does it cost?" },
];
const boot = (over: Record<string, unknown> = {}) => ({
  ...BOOT,
  starters: TILES,
  footer_links: [
    { link_id: "TALK-TO-EXPERT", label: "Talk to an expert" },
    { link_id: "PRIVACY-NOTICE", label: "Privacy & trust" },
  ],
  ...over,
});

const defaults = () => baseRoutes({ "GET /bootstrap": { status: 200, body: boot() }, "POST /messages": { status: 200, body: packet() } });

async function open(routes = defaults(), props: Record<string, unknown> = {}) {
  const api = mockApi(routes);
  const user = userEvent.setup();
  render(<AssistantWidget apiBase={BASE} fetchImpl={api.fetchImpl} {...props} />);
  await user.click(screen.getByRole("button", { name: /open the zoiko id assistant/i }));
  return { api, user };
}

async function ask(user: ReturnType<typeof userEvent.setup>, text: string) {
  await user.type(await screen.findByLabelText(/ask a question/i), text);
  await user.click(screen.getByRole("button", { name: /^send$/i }));
}

describe("the design board layout", () => {
  it("shows the welcome card, topic tiles with descriptions, and audience shortcuts", async () => {
    await open();
    expect(await screen.findByRole("heading", { name: "Welcome to Zoiko iD.", level: 3 })).toBeTruthy();
    const tiles = within(screen.getByRole("group", { name: /try one of these/i }));
    expect(tiles.getAllByRole("button").length).toBe(4);
    expect(tiles.getByRole("button", { name: /authentication & passkeys.*passwordless, mfa/i })).toBeTruthy();
    for (const a of ["Developers", "Enterprises", "Individuals"]) expect(screen.getByRole("button", { name: a })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Pricing" })).toBeTruthy(); // a starter without an icon is a plain chip
  });

  it("a tile just sends its question", async () => {
    const seen: string[] = [];
    const routes = defaults();
    routes["POST /messages"] = ({ body }) => {
      seen.push((body as { text: string }).text);
      return { status: 200, body: packet() };
    };
    const { user } = await open(routes);
    await user.click(await screen.findByRole("button", { name: /authentication & passkeys/i }));
    await screen.findByRole("article");
    expect(seen).toEqual(["How do passkeys work?"]);
    expect(screen.getByText("How do passkeys work?")).toBeTruthy(); // the question is in the transcript
  });

  it("an audience shortcut sends a visible question too, and builds no hidden profile", async () => {
    const seen: unknown[] = [];
    const routes = defaults();
    routes["POST /messages"] = ({ body }) => {
      seen.push(body);
      return { status: 200, body: packet() };
    };
    const { user } = await open(routes);
    await user.click(await screen.findByRole("button", { name: "Developers" }));
    await screen.findByRole("article");
    expect(Object.keys(seen[0] as object).sort()).toEqual(["client_message_id", "locale", "text"]); // no audience field, no context
    expect((seen[0] as { text: string }).text).toBe("How do developers integrate Zoiko iD?");
    expect(screen.getByText("How do developers integrate Zoiko iD?")).toBeTruthy();
  });

  it("a host can remove the audience shortcuts", async () => {
    await open(undefined, { audiences: [] });
    await screen.findByRole("heading", { name: "Welcome to Zoiko iD.", level: 3 });
    expect(screen.queryByRole("button", { name: "Developers" })).toBeNull();
  });

  it("puts the host's logo in the header, the welcome card and beside replies", async () => {
    const { user } = await open(undefined, { logo: <svg data-testid="brand" /> });
    await screen.findByRole("heading", { name: "Welcome to Zoiko iD.", level: 3 });
    expect(screen.getAllByTestId("brand").length).toBe(3); // launcher + header + welcome card
    await ask(user, "hi");
    await screen.findByRole("article");
    expect(document.querySelector(".zpa-avatar-bot [data-testid=brand]")).not.toBeNull(); // the reply's avatar carries it (the welcome card has gone)
  });

  it("without a host logo it draws no brand mark of its own", async () => {
    await open();
    const header = (await screen.findByRole("dialog")).querySelector("header") as HTMLElement;
    expect(header.querySelector("img")).toBeNull();
    expect(header.querySelector(".zpa-logo svg")).not.toBeNull(); // the neutral chat glyph
    expect(header.querySelector(".zpa-logo")?.textContent).toBe(""); // no letters or wordmark
  });

  it("shows a time under the visitor's message and the reply", async () => {
    const { user } = await open();
    await ask(user, "hello");
    await screen.findByRole("article");
    const times = document.querySelectorAll("time.zpa-time");
    expect(times.length).toBe(2);
    for (const el of times) expect(el.textContent).toMatch(/\d{1,2}:\d{2}/);
  });

  it("labels a restricted reply", async () => {
    const { user } = await open(baseRoutes({ "GET /bootstrap": { status: 200, body: boot() }, "POST /messages": { status: 200, body: packet({ decision: "ABSTAIN" }) } }));
    await ask(user, "x");
    expect((await screen.findByRole("article")).querySelector(".zpa-badge")).not.toBeNull();
  });

  it("shows no badge on a plain answer (its label stays in the accessible name)", async () => {
    const { user } = await open();
    await ask(user, "x");
    const card = await screen.findByRole("article");
    expect(card.querySelector(".zpa-badge")).toBeNull();
    expect(card.getAttribute("aria-label")).toMatch(/Answer/);
  });
});

describe("the status is real, never hard-coded", () => {
  it("says Connecting while loading and Online once the service answers", async () => {
    let release: () => void = () => undefined;
    const gate = new Promise<void>((r) => {
      release = r;
    });
    await open(baseRoutes({ "GET /bootstrap": { status: 200, body: boot(), delay: gate } }));
    expect(await screen.findByText("Connecting…")).toBeTruthy();
    expect(screen.queryByText("Online")).toBeNull();
    release();
    expect(await screen.findByText("Online")).toBeTruthy();
  });

  it("says Offline when the service is switched off", async () => {
    await open(baseRoutes({ "GET /bootstrap": { status: 200, body: boot({ enabled: false }) } }));
    expect(await screen.findByText("Offline")).toBeTruthy();
    expect(screen.queryByText("Online")).toBeNull();
  });

  it("says Unavailable when it cannot be reached", async () => {
    await open(baseRoutes({ "GET /bootstrap": { status: 503, body: { error: { code: "UNAVAILABLE", message: "x" } } } }));
    expect(await screen.findByText("Unavailable")).toBeTruthy();
    expect(screen.queryByText("Online")).toBeNull();
  });
});

describe("no way to send a file", () => {
  it("has no attach, upload or file control anywhere in the panel", async () => {
    await open();
    await screen.findByLabelText(/ask a question/i);
    const dialog = screen.getByRole("dialog");
    expect(dialog.querySelector("input[type='file']")).toBeNull();
    expect(within(dialog).queryByRole("button", { name: /attach|upload|file|paperclip|image|photo/i })).toBeNull();
    expect(dialog.querySelector("[accept], [capture]")).toBeNull();
  });
});

describe("minimize keeps the chat, close ends it", () => {
  it("minimize hides the panel and the conversation is still there when it reopens", async () => {
    const { user, api } = await open();
    await ask(user, "hello there");
    await screen.findByRole("article");
    await user.click(screen.getByRole("button", { name: "Minimize" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    await user.click(screen.getByRole("button", { name: /open the zoiko id assistant/i }));
    expect(await screen.findByRole("article")).toBeTruthy();
    expect(api.calls.some((c) => c.method === "DELETE")).toBe(false);
  });

  it("close forgets the session on the server and clears everything shown", async () => {
    const { user, api } = await open();
    await ask(user, "private question");
    await screen.findByRole("article");
    await user.click(screen.getByRole("button", { name: "Close" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(api.calls.some((c) => c.method === "DELETE" && c.path === "/sessions")).toBe(true);
    await user.click(screen.getByRole("button", { name: /open the zoiko id assistant/i }));
    await screen.findByLabelText(/ask a question/i);
    expect(screen.queryByRole("article")).toBeNull();
    expect(screen.queryByText("private question")).toBeNull();
  });
});

describe("the footer", () => {
  it("links come only from server-issued ids, through /out/{link_id}", async () => {
    await open();
    const footer = await screen.findByRole("navigation", { name: /more help/i });
    const hrefs = within(footer).getAllByRole("link").map((a) => a.getAttribute("href"));
    expect(hrefs).toEqual([`${BASE}/out/TALK-TO-EXPERT`, `${BASE}/out/PRIVACY-NOTICE`]);
    for (const a of within(footer).getAllByRole("link")) expect(a.getAttribute("rel")).toContain("noopener");
  });
});

describe("new chat and history", () => {
  it("new chat clears the screen, ends the server session and keeps the old chat in this window's history", async () => {
    const { user, api } = await open();
    await ask(user, "first question");
    await screen.findByRole("article");
    await user.click(screen.getByRole("button", { name: "New chat" }));
    await waitFor(() => expect(screen.queryByRole("article")).toBeNull());
    expect(api.calls.some((c) => c.method === "DELETE" && c.path === "/sessions")).toBe(true);
    expect(screen.queryByText("first question")).toBeNull();
    await user.click(screen.getByRole("button", { name: "History" }));
    await user.click(await screen.findByRole("button", { name: /first question/ }));
    expect(within(screen.getByRole("log", { name: /earlier conversation/i })).getByText(/first question/)).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "All earlier chats" }));
    expect(await screen.findByRole("button", { name: /first question/ })).toBeTruthy();
  });

  it("history starts empty, and an empty chat is not archived", async () => {
    const { user } = await open();
    await screen.findByLabelText(/ask a question/i);
    await user.click(screen.getByRole("button", { name: "New chat" }));
    await user.click(screen.getByRole("button", { name: "History" }));
    expect(await screen.findByText(/no earlier chats/i)).toBeTruthy();
  });

  it("close forgets the history too", async () => {
    const { user } = await open();
    await ask(user, "secret topic");
    await screen.findByRole("article");
    await user.click(screen.getByRole("button", { name: "New chat" }));
    await user.click(screen.getByRole("button", { name: "Close" }));
    await user.click(screen.getByRole("button", { name: /open the zoiko id assistant/i }));
    await user.click(await screen.findByRole("button", { name: "History" }));
    expect(await screen.findByText(/no earlier chats/i)).toBeTruthy();
    expect(screen.queryByText("secret topic")).toBeNull();
  });

  it("writes nothing to browser storage", async () => {
    const set = vi.spyOn(Storage.prototype, "setItem");
    const { user } = await open();
    await ask(user, "hello");
    await screen.findByRole("article");
    await user.click(screen.getByRole("button", { name: "New chat" }));
    expect(set).not.toHaveBeenCalled();
  });
});

describe("the answer flows in", () => {
  it("shows the text letter by letter, gives assistive tech the whole text at once, and ends with the full answer", async () => {
    const long = "Verifiable credentials are signed statements that a holder can present and a verifier can check without asking the issuer again.";
    const { user } = await open(baseRoutes({ "GET /bootstrap": { status: 200, body: boot() }, "POST /messages": { status: 200, body: packet({ answer_text: long }) } }));
    await ask(user, "x");
    const card = await screen.findByRole("article");
    expect(card.querySelector(".zpa-answer .zpa-sr")?.textContent).toBe(long); // whole text for screen readers
    expect(card.querySelector(".zpa-answer [aria-hidden='true']")).not.toBeNull(); // the animated copy is hidden from them
    await waitFor(() => expect(card.querySelector(".zpa-answer .zpa-sr")).toBeNull(), { timeout: 4000 }); // done: plain text
    expect(card.querySelector(".zpa-answer")?.textContent).toBe(long);
  });

  it("does not replay when the panel is minimized and reopened", async () => {
    const { user } = await open();
    await ask(user, "x");
    const card = await screen.findByRole("article");
    await waitFor(() => expect(card.querySelector(".zpa-sr")).toBeNull(), { timeout: 4000 });
    await user.click(screen.getByRole("button", { name: "Minimize" }));
    await user.click(screen.getByRole("button", { name: /open the zoiko id assistant/i }));
    expect((await screen.findByRole("article")).querySelector(".zpa-answer .zpa-sr")).toBeNull();
  });
});
