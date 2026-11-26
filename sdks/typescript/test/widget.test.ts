// @vitest-environment jsdom
import { describe, expect, it } from "vitest";

import { mount, widgetWording, type SourceAnswer, type WidgetHost } from "../src/widget.js";

function setup(host: Partial<WidgetHost> = {}, locale?: string) {
  document.body.innerHTML = "";
  const container = document.createElement("div");
  document.body.append(container);
  const events: string[] = [];
  const captured: string[] = [];
  const full: WidgetHost = {
    capture: async (f) => {
      captured.push(f.name);
      return { intact: true };
    },
    awaitSource: async () => "UNAVAILABLE",
    ...host,
  };
  const handle = mount(container, { host: full, locale, consentText: "We will read this document to check it with its issuer.", onEvent: (e) => events.push(e) });
  const click = (key: string) => (container.querySelector(`[data-zid="${key}"]`) as HTMLButtonElement).click();
  const choose = (name = "licence.pdf") => {
    const input = container.querySelector("input[type=file]") as HTMLInputElement;
    Object.defineProperty(input, "files", { value: [new File(["x"], name)], configurable: true });
  };
  const tick = () => new Promise((r) => setTimeout(r, 0));
  return { container, handle, events, captured, click, choose, tick };
}

describe("browser widget (ZID-INT-002 section 05, session.mount)", () => {
  it("collects nothing before consent, and there is no file input until the person agrees", () => {
    const w = setup();
    expect(w.handle.state()).toBe("CONSENT_REQUIRED");
    expect(w.container.querySelector("input[type=file]")).toBeNull();
    expect(w.container.textContent).toContain("We will read this document");
    expect(w.captured).toEqual([]);
  });

  it("refuses to mount without consent wording", () => {
    const c = document.createElement("div");
    expect(() => mount(c, { host: { capture: async () => ({ intact: true }), awaitSource: async () => "CONFIRMED" }, consentText: "  " })).toThrow();
  });

  it("declining ends the session with nothing collected", () => {
    const w = setup();
    w.click("decline");
    expect(w.handle.state()).toBe("DECLINED");
    expect(w.container.querySelector("input[type=file]")).toBeNull();
    expect(w.container.textContent).toContain("Nothing was collected");
  });

  it("an issuer outage keeps the person waiting and is never shown as a failure", async () => {
    const w = setup();
    w.click("agree");
    w.choose();
    w.click("send");
    await w.tick();
    await w.tick();
    expect(w.handle.state()).toBe("SOURCE_PENDING");
    expect(w.captured).toEqual(["licence.pdf"]);
    expect(w.container.querySelector(".zid-status")?.textContent).toMatch(/not a refusal/);
    expect(w.container.textContent).not.toMatch(/fail|verified|approved/i);
    expect(w.events).toEqual(["session.created", "consent.recorded", "capture.started", "document.submitted", "issuer.pending"]);
  });

  it("an unreadable file asks to choose it again, with the error announced, and decides nothing", async () => {
    const w = setup({ capture: async () => ({ intact: false }) });
    w.click("agree");
    w.choose();
    w.click("send");
    await w.tick();
    await w.tick();
    expect(w.handle.state()).toBe("RETRY_REQUIRED");
    const alert = w.container.querySelector("[role=alert]");
    expect(alert?.textContent).toMatch(/Nothing was decided/);
    expect(w.container.querySelector("input[type=file]")).not.toBeNull();
  });

  it("a failed send does not move the session on", async () => {
    const w = setup({
      capture: async () => {
        throw new Error("network");
      },
    });
    w.click("agree");
    w.choose();
    w.click("send");
    await w.tick();
    await w.tick();
    expect(w.handle.state()).toBe("COLLECTING");
    expect(w.container.querySelector("[role=alert]")?.textContent).toMatch(/try again/i);
  });

  it("an unclear source answer goes to a person, and a confirmed one completes", async () => {
    for (const [answer, state] of [["UNCLEAR", "REVIEW_REQUIRED"], ["CONFIRMED", "COMPLETE"], ["TIMED_OUT", "EXPIRED"]] as [SourceAnswer, string][]) {
      const w = setup({ awaitSource: async () => answer });
      w.click("agree");
      w.choose();
      w.click("send");
      for (let i = 0; i < 4; i++) await w.tick();
      expect(w.handle.state()).toBe(state);
    }
  });

  it("consent can be withdrawn while capture is open, and capture stops", () => {
    const w = setup();
    w.click("agree");
    w.click("withdraw");
    expect(w.handle.state()).toBe("CANCELED");
    expect(w.container.querySelector("input[type=file]")).toBeNull();
    expect(w.events.at(-1)).toBe("session.canceled");
  });

  it("is built for keyboard and screen reader use with native controls", () => {
    const w = setup();
    w.click("agree");
    const region = w.container.querySelector("[role=region]")!;
    const heading = w.container.querySelector("h2")!;
    expect(region.getAttribute("aria-labelledby")).toBe(heading.id);
    expect(w.container.querySelector("[role=status]")?.getAttribute("aria-live")).toBe("polite");
    const input = w.container.querySelector("input[type=file]") as HTMLInputElement;
    const label = w.container.querySelector(`label[for="${input.id}"]`);
    expect(label?.textContent).toBe("Choose a document");
    expect(Array.from(w.container.querySelectorAll("button")).every((b) => b.getAttribute("type") === "button")).toBe(true);
    expect(w.container.querySelectorAll("[style]").length).toBe(0);
    expect(document.activeElement).toBe(heading);
  });

  it("puts text in as text, never as markup", () => {
    document.body.innerHTML = "";
    const c = document.createElement("div");
    document.body.append(c);
    mount(c, { host: { capture: async () => ({ intact: true }), awaitSource: async () => "CONFIRMED" }, consentText: "<img src=x onerror=alert(1)>" });
    expect(c.querySelector("img")).toBeNull();
    expect(c.textContent).toContain("<img src=x");
  });

  it("speaks Spanish and French as drafts, and falls back to English rather than a blank", () => {
    expect(widgetWording("en").reviewed).toBe(true);
    expect(widgetWording("es-MX").reviewed).toBe(false);
    expect(widgetWording("fr").heading).toBe("Vérifiez votre document");
    expect(widgetWording("xx").heading).toBe("Check your document");
    const w = setup({}, "es");
    expect(w.container.querySelector("h2")?.textContent).toBe("Compruebe su documento");
    expect(w.container.querySelector("section")?.getAttribute("lang")).toBe("es");
  });

  it("every state wording exists in every language", () => {
    const keys = Object.keys(widgetWording("en").states).sort();
    for (const l of ["es", "fr"]) expect(Object.keys(widgetWording(l).states).sort()).toEqual(keys);
  });

  it("destroy removes the widget and stops reacting", () => {
    const w = setup();
    w.handle.destroy();
    expect(w.container.querySelector("section")).toBeNull();
  });
});

describe("what the widget promises about resuming and about finishing (ZID-CP-WF10 v2.0 rows 6-05 and 7-05)", () => {
  it("after a dropped connection the person retries in the same session: no second session, and the opening events are not repeated", async () => {
    let calls = 0;
    const w = setup({
      capture: async (f) => {
        calls += 1;
        if (calls === 1) throw new Error("network down");
        w.captured.push(f.name);
        return { intact: true };
      },
      awaitSource: async () => "CONFIRMED",
    });
    w.click("agree");
    w.choose();
    w.click("send");
    for (let i = 0; i < 4; i++) await w.tick();
    expect(w.handle.state()).toBe("COLLECTING"); // the failed send decided nothing and left the person where they were
    w.click("send");
    for (let i = 0; i < 6; i++) await w.tick();
    expect(calls).toBe(2);
    expect(w.handle.state()).toBe("COMPLETE");
    expect(w.events.filter((e) => e === "session.created")).toHaveLength(1); // one session for the whole attempt
    expect(w.events.filter((e) => e === "capture.started")).toHaveLength(1);
    expect(w.events.filter((e) => e === "document.submitted")).toHaveLength(1); // only the send that arrived counts
  });

  it("completing the widget never says the verification is final: the only closing event is session.completed, and no event or payload carries a decision", async () => {
    const seen: unknown[][] = [];
    document.body.innerHTML = "";
    const container = document.createElement("div");
    document.body.append(container);
    mount(container, {
      host: { capture: async () => ({ intact: true }), awaitSource: async () => "CONFIRMED" },
      consentText: "We will read this document to check it with its issuer.",
      onEvent: (...args: unknown[]) => seen.push(args),
    });
    (container.querySelector('[data-zid="agree"]') as HTMLButtonElement).click();
    const input = container.querySelector("input[type=file]") as HTMLInputElement;
    Object.defineProperty(input, "files", { value: [new File(["x"], "a.pdf")], configurable: true });
    (container.querySelector('[data-zid="send"]') as HTMLButtonElement).click();
    for (let i = 0; i < 6; i++) await new Promise((r) => setTimeout(r, 0));
    const names = seen.map((a) => String(a[0]));
    expect(names[names.length - 1]).toBe("session.completed");
    expect(names.filter((n) => /final|decid|approv|verified|result|pass/i.test(n))).toEqual([]); // no event name claims an outcome
    expect(seen.every((a) => a.length === 2 && typeof a[0] === "string" && typeof a[1] === "string")).toBe(true); // an event and a session state; no decision, score or reason rides along
  });
});
