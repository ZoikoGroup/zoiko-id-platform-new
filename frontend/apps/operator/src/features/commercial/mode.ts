"use client";

import { useCellQuery } from "@/lib/op";

/** Whether this cell runs the operator plane in direct mode (one operator account manages everything that does not
 * touch customer data, with no access request or second approver). Unknown until loaded: treated as governed. */
export function useDirectMode(): boolean {
  const caps = useCellQuery(["operator", "capabilities"], (api) => api.capabilities());
  return caps.data?.mode === "direct";
}
