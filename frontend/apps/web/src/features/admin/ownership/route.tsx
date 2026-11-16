"use client";

import { useMe } from "../authority/shared";
import { OwnershipDetailPage } from "./ownership";

/** Supplies who is signed in, so the page can say "you recorded this, someone else decides" before the platform has to refuse. */
export function OwnershipDetailRoute({ graphId }: { graphId: string }) {
  return <OwnershipDetailPage graphId={graphId} me={useMe()} />;
}
