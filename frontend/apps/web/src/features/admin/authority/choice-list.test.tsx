/** The grant dialog's pickers: ticking a feature area covers all of it, the lists stay in step, and nothing typed by hand is lost. */
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/cell", () => ({
  listInput: (v: string) =>
    v
      .split(/[\n,]/)
      .map((s) => s.trim())
      .filter(Boolean),
}));

vi.mock("@zoiko/ui", () => ({
  Checkbox: ({
    children,
    isSelected,
    isIndeterminate,
    onChange,
  }: {
    children: ReactNode;
    isSelected?: boolean;
    isIndeterminate?: boolean;
    onChange?: (on: boolean) => void;
  }) => (
    <label>
      <input
        type="checkbox"
        checked={Boolean(isSelected)}
        data-indeterminate={isIndeterminate ? "true" : "false"}
        onChange={(e) => onChange?.(e.target.checked)}
      />
      {children}
    </label>
  ),
  TextField: ({ label, value, onChange }: { label: string; value: string; onChange: (v: string) => void }) => (
    <label>
      {label}
      <input aria-label={label} value={value} onChange={(e) => onChange(e.target.value)} />
    </label>
  ),
}));

import { ACTION_CHOICES, ACTION_GROUPS, AreaPicker, ChoiceList, RESOURCE_CHOICES, RESOURCE_GROUPS } from "./choice-list";

afterEach(cleanup);

function Harness({ initialActions = "", initialResources = "" }: { initialActions?: string; initialResources?: string }) {
  const [actions, setActions] = useState(initialActions);
  const [resources, setResources] = useState(initialResources);
  return (
    <>
      <AreaPicker actions={actions} resources={resources} onActions={setActions} onResources={setResources} />
      <ChoiceList label="Actions" description="" groups={ACTION_GROUPS} value={actions} onChange={setActions} otherLabel="Another action name" otherPlaceholder="x" />
      <ChoiceList label="Resources" description="" groups={RESOURCE_GROUPS} value={resources} onChange={setResources} otherLabel="Another resource" otherPlaceholder="y" />
      <output data-testid="actions">{actions}</output>
      <output data-testid="resources">{resources}</output>
    </>
  );
}

const picker = () => within(screen.getByText("What it covers").closest("fieldset") as HTMLElement);
const listOf = (id: string) => screen.getByTestId(id).textContent!.split("\n").filter(Boolean);

describe("the catalogue the dialog offers", () => {
  it("has an area for every set of actions, and the same areas for resources", () => {
    expect(RESOURCE_GROUPS.map((g) => g.id)).toEqual(ACTION_GROUPS.map((g) => g.id));
  });

  it("names every action and resource once, in the platform's own form", () => {
    const actions = ACTION_GROUPS.flatMap((g) => g.choices.map((c) => c.id));
    const resources = RESOURCE_GROUPS.flatMap((g) => g.choices.map((c) => c.id));
    expect(new Set(actions).size).toBe(actions.length);
    expect(new Set(resources).size).toBe(resources.length);
    for (const a of actions) expect(a).toMatch(/^[a-z_]+(\.[a-z_]+)+$/);
    for (const r of resources) expect(r).toMatch(/^[a-z_]+:\*$/);
  });

  it("covers the agent and workload features the Agents and Workload identity screens need", () => {
    const actions = new Set(ACTION_GROUPS.flatMap((g) => g.choices.map((c) => c.id)));
    for (const a of ["machine.agent.manage", "machine.agent.activate", "machine.blueprint.manage", "machine.tool.manage", "machine.workload.manage"]) {
      expect(actions.has(a), a).toBe(true);
    }
    const resources = new Set(RESOURCE_GROUPS.flatMap((g) => g.choices.map((c) => c.id)));
    for (const r of ["agent:*", "blueprint:*", "tool:*", "workload_binding:*", "workload_environment:*"]) expect(resources.has(r), r).toBe(true);
  });

  it("leaves the mandate dialog's spending-only lists as they were", () => {
    expect(ACTION_CHOICES.map((c) => c.id)).toEqual(["payment.release", "mandate.create", "purchase.create", "invoice.approve"]);
    expect(RESOURCE_CHOICES.map((c) => c.id)).toEqual(["payment:*", "mandate:*", "purchase:*", "invoice:*"]);
  });
});

describe("ticking a feature area", () => {
  it("ticks its actions and its resources together, and clears both again", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await user.click(picker().getByLabelText(/Agents and tools/));
    expect(listOf("actions")).toEqual(
      expect.arrayContaining(["machine.agent.read", "machine.agent.manage", "machine.agent.activate", "machine.blueprint.manage", "machine.tool.manage"]),
    );
    expect(listOf("resources")).toEqual(expect.arrayContaining(["agent:*", "blueprint:*", "tool:*"]));
    expect(listOf("actions")).not.toContain("payment.release");

    await user.click(picker().getByLabelText(/Agents and tools/));
    expect(listOf("actions")).toEqual([]);
    expect(listOf("resources")).toEqual([]);
  });

  it("shows a partly ticked area as indeterminate and finishes it from the area box", async () => {
    const user = userEvent.setup();
    render(<Harness initialActions={"machine.tool.manage"} />);
    const box = picker().getByLabelText(/Agents and tools/);
    expect(box.getAttribute("data-indeterminate")).toBe("true");
    await user.click(box);
    expect(listOf("actions")).toContain("machine.agent.manage");
    expect(listOf("resources")).toContain("agent:*");
  });

  it("'All areas' ticks every area and unticks them all", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await user.click(picker().getByLabelText("All areas"));
    expect(listOf("actions")).toHaveLength(ACTION_GROUPS.flatMap((g) => g.choices).length);
    expect(listOf("resources")).toHaveLength(RESOURCE_GROUPS.flatMap((g) => g.choices).length);
    await user.click(picker().getByLabelText("All areas"));
    expect(listOf("actions")).toEqual([]);
  });

  it("never loses what was typed under 'Something else', nor another area's ticks", async () => {
    const user = userEvent.setup();
    render(<Harness initialActions={"payment.release\ncustom.thing"} initialResources={"payment:*\ncustom:id"} />);
    await user.click(picker().getByLabelText(/Workload identity/));
    expect(listOf("actions")).toEqual(expect.arrayContaining(["payment.release", "machine.workload.manage", "custom.thing"]));
    expect(listOf("resources")).toEqual(expect.arrayContaining(["payment:*", "workload_binding:*", "workload_environment:*", "custom:id"]));
    await user.click(picker().getByLabelText(/Workload identity/));
    expect(listOf("actions")).toEqual(expect.arrayContaining(["payment.release", "custom.thing"]));
    expect(listOf("actions")).not.toContain("machine.workload.manage");
  });
});

describe("the grouped lists", () => {
  it("tick and clear a whole group, and a single choice, independently", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    const actions = within(screen.getByText("Actions").closest("fieldset") as HTMLElement);
    await user.click(within(actions.getByRole("group", { name: "Evidence and audit" })).getByLabelText(/Evidence and audit/));
    expect(listOf("actions")).toEqual(expect.arrayContaining(["evidence.read", "evidence.verify", "evidence.annotate", "evidence.export"]));
    await user.click(actions.getByLabelText(/Export a signed evidence package/));
    expect(listOf("actions")).not.toContain("evidence.export");
    expect(listOf("actions")).toContain("evidence.read");
  });
});
