"use client";

import { sendGlobalPrivacySignal } from "@zoiko/ui";
import { useEffect } from "react";

import { useRuntime } from "@/lib/runtime";

const memo = { sent: false }; // once per page load; no web storage is used

/** Sends the browser's Global Privacy Control signal to the cell (PRIV-21). Renders nothing. */
export function PrivacySignal() {
  const { api } = useRuntime();
  useEffect(() => {
    void sendGlobalPrivacySignal({
      gpc: (navigator as Navigator & { globalPrivacyControl?: boolean }).globalPrivacyControl,
      memo,
      send: () => api.privacy.preferenceSignal(),
    });
  }, [api]);
  return null;
}
