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
