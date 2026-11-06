import { describe, expect, it } from "vitest";

import { SERVICES, serviceLabel } from "./service-requests";

describe("service requests", () => {
  it("names each of the four contracted services", () => {
    expect(SERVICES.map((s) => s.id).sort()).toEqual(["DEDICATED_ISOLATION", "KEY_CUSTODY", "NAMED_SUPPORT", "SOVEREIGN_SERVICE"]);
    expect(serviceLabel("KEY_CUSTODY")).toMatch(/BYOK/);
  });
});
