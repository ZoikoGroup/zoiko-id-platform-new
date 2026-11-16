/** GID-062, GLB-24: a person's name is free text someone else wrote, so it is isolated from the account id beside it. */
import type { Person } from "@zoiko/api-client";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { PersonLabel } from "./person-label";

afterEach(cleanup);

describe("PersonLabel", () => {
  it("isolates a right-to-left name so it cannot reorder the id next to it", () => {
    const person = { principal_id: "p-1", label: "عبد الله (M-0007)" } as unknown as Person;
    const { container } = render(<PersonLabel person={person} />);
    const isolated = container.querySelector("bdi");
    expect(isolated).not.toBeNull();
    expect(isolated?.getAttribute("dir")).toBe("auto");
    expect(isolated?.textContent).toBe("عبد الله (M-0007)");
  });

  it("changes nothing visible for a Latin name", () => {
    const person = { principal_id: "p-2", label: "Dana Lee (M-0008)" } as unknown as Person;
    render(<PersonLabel person={person} />);
    expect(screen.getByText("Dana Lee (M-0008)")).toBeTruthy();
  });
});
