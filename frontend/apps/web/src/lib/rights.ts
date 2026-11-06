"use client";

import { useCellQuery } from "@/lib/cell";
import { can } from "@/lib/nav-access";

/** What the signed-in person's roles could do, from the role matrix (one shared request). Screens use it to hide controls the cell would refuse;
 * the cell still decides every request, and this is `undefined` until it loads, so nothing risky is shown early. */
export function useRights() {
  const q = useCellQuery(["tenant", "permissions", "rights"], (api) => api.tenant.myPermissions());
  const rights = q.data?.rights;
  return { rights, loaded: q.data !== undefined, can: (action: string, ...modes: string[]) => can(rights, action, ...modes) };
}
