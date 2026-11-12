/** Names that someone else wrote must not reorder the text around them (GID-062, GLB-24): Arabic, Hebrew and mixed-script names. */
import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { UserText, textDirection } from "./user-text";

afterEach(cleanup);

const NAMES = {
  arabic: "جامعة الملك سعود",
  hebrew: "האוניברסיטה העברית בירושלים",
  latin: "University of Exampleton",
  devanagari: "भारतीय प्रौद्योगिकी संस्थान",
  thai: "มหาวิทยาลัยเชียงใหม่",
  cjk: "東京大学",
  mixed: "جامعة Exampleton (2024)",
};

describe("UserText", () => {
  it("isolates a name so it cannot reorder its neighbours, and lets the browser pick its direction", () => {
    for (const [, name] of Object.entries(NAMES)) {
      const { container, unmount } = render(
        <p>
          Awarded by <UserText>{name}</UserText>, 2024
        </p>,
      );
      const el = container.querySelector("bdi");
      expect(el).not.toBeNull();
      expect(el?.getAttribute("dir")).toBe("auto");
      expect(el?.textContent).toBe(name);
      unmount();
    }
  });

  it("keeps the surrounding sentence as it was", () => {
    const { container } = render(
      <p>
        Awarded by <UserText>{NAMES.arabic}</UserText>, 2024
      </p>,
    );
    expect(container.textContent).toBe(`Awarded by ${NAMES.arabic}, 2024`);
  });

  it("passes a class through so it can sit in a styled cell", () => {
    const { container } = render(<UserText className="font-medium">{NAMES.hebrew}</UserText>);
    expect(container.querySelector("bdi")?.className).toBe("font-medium");
  });
});

describe("textDirection", () => {
  it("reads Arabic and Hebrew as right to left and everything else as left to right", () => {
    expect(textDirection(NAMES.arabic)).toBe("rtl");
    expect(textDirection(NAMES.hebrew)).toBe("rtl");
    for (const key of ["latin", "devanagari", "thai", "cjk"] as const) {
      expect(textDirection(NAMES[key])).toBe("ltr");
    }
  });
  it("takes the first letter that has a direction, so digits and punctuation in front do not decide it", () => {
    expect(textDirection(`(2024) ${NAMES.arabic}`)).toBe("rtl");
    expect(textDirection(`"${NAMES.latin}"`)).toBe("ltr");
    expect(textDirection(NAMES.mixed)).toBe("rtl");
  });
  it("treats empty and neutral-only text as left to right", () => {
    expect(textDirection("")).toBe("ltr");
    expect(textDirection("123 - 456")).toBe("ltr");
  });
});
