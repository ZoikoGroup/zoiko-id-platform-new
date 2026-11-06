import type { OrgUnit } from "@zoiko/api-client";
import { describe, expect, it } from "vitest";

import { inTreeOrder } from "./org-structure-tab";

const u = (id: string, parent: string | null, name: string, depth: number): OrgUnit => ({ unit_id: id, parent_unit_id: parent, name, kind: "DEPARTMENT", depth, members: 0 });

describe("inTreeOrder", () => {
  it("puts each unit after its parent and sorts siblings by name", () => {
    const list = inTreeOrder([u("c", "a", "Zed", 1), u("b", null, "Beta", 0), u("a", null, "Alpha", 0), u("d", "a", "Ace", 1)]);
    expect(list.map((x) => x.name)).toEqual(["Alpha", "Ace", "Zed", "Beta"]);
  });
});
