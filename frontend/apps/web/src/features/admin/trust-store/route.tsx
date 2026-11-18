"use client";

import { useMe } from "../authority/shared";
import { TrustStorePage } from "./trust-store";

/** Supplies who is signed in, so the page can say "you staged this, someone else decides" before the platform has to refuse. */
export function TrustStoreRoute() {
  return <TrustStorePage me={useMe()} />;
}
