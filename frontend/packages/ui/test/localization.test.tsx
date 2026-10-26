// @vitest-environment jsdom
/** UX-31: dates and amounts follow the person's locale, and a long translated label wraps instead of being cut off. */
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { Button, DateTime, LocaleProvider, Money, NumberValue, resolveLocale } from "../src";

afterEach(cleanup);

describe("UX-31 locale: the language is chosen from the browser, safely", () => {
  it.each([
    ["de-DE,de;q=0.9,en;q=0.8", "de-DE"],
    ["fr;q=0.4, en-GB;q=0.9", "en-GB"], // the higher q wins
    ["*", "en-US"],
    ["", "en-US"],
    [null, "en-US"],
    ["xx-not-a-real-tag-!!!", "en-US"], // malformed: skipped, never throws
  ])("%s -> %s", (header, expected) => {
    expect(resolveLocale(header as string | null)).toBe(expected);
  });
});

describe("UX-31 formats follow the locale", () => {
  const amount = (locale: string) => {
    render(
      <LocaleProvider locale={locale}>
        <Money minor={123456} currency="EUR" />
      </LocaleProvider>,
    );
    return screen.getByText(/EUR|€/).textContent ?? "";
  };

  it("writes the same amount the way each locale does, keeping the currency explicit", () => {
    const us = amount("en-US");
    cleanup();
    const de = amount("de-DE");
    expect(us).toContain("1,234.56");
    expect(de).toContain("1.234,56"); // decimal comma, dot for thousands
    expect(us).toContain("EUR");
    expect(de).toContain("EUR");
  });

  it("writes dates in the locale's order", () => {
    const at = "2026-10-26T12:00:00Z";
    const { container, rerender } = render(
      <LocaleProvider locale="en-US">
        <DateTime value={at} style="date" />
      </LocaleProvider>,
    );
    const us = container.textContent;
    rerender(
      <LocaleProvider locale="de-DE">
        <DateTime value={at} style="date" />
      </LocaleProvider>,
    );
    expect(container.textContent).not.toBe(us);
    expect(container.textContent).toMatch(/26/);
  });

  it("formats plain numbers per locale", () => {
    const { container } = render(
      <LocaleProvider locale="de-DE">
        <NumberValue value={1234567} />
      </LocaleProvider>,
    );
    expect(container.textContent).toBe("1.234.567");
  });
});

describe("UX-31 a long translated label does not clip a critical control", () => {
  const long = "Bestätigung der Änderung der Wohnsitzeinstellungen für das gesamte Unternehmen jetzt absenden";

  it("lets the label wrap and the button grow, rather than forcing one line", () => {
    render(<Button>{long}</Button>);
    const cls = screen.getByRole("button").className;
    expect(cls).toContain("whitespace-normal");
    expect(cls).not.toContain("whitespace-nowrap");
    expect(cls).not.toMatch(/(^|\s)h-\d/); // a fixed height would cut a second line
    expect(cls).toMatch(/min-h-\d/);
    expect(cls).not.toContain("truncate");
    expect(cls).not.toContain("overflow-hidden");
  });
});
