// @vitest-environment jsdom
/** UX-29: while an operation runs its button cannot be pressed again, so a slow export or replay is not started twice. */
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { Button } from "../src";

afterEach(cleanup);

describe("UX-29 a running operation cannot be started again", () => {
  it("ignores presses while pending and accepts them again afterwards", async () => {
    const press = vi.fn();
    const { rerender } = render(
      <Button pending onPress={press}>
        Export
      </Button>,
    );
    await userEvent.click(screen.getByRole("button", { name: /export/i }));
    expect(press).not.toHaveBeenCalled();
    rerender(<Button onPress={press}>Export</Button>);
    await userEvent.click(screen.getByRole("button", { name: /export/i }));
    expect(press).toHaveBeenCalledTimes(1);
  });
});
