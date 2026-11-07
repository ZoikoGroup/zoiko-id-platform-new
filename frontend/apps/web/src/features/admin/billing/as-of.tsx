"use client";

import { RelativeTime } from "@zoiko/ui";

/** "Data as of ..." for a list: the moment this page last read it from the cell, so a stale view is never mistaken for live (B-wireframe 07). */
export function AsOf({ at }: { at: number }) {
  if (!at) return null;
  return (
    <p className="text-xs text-ink-subtle">
      Data as of <RelativeTime value={new Date(at).toISOString()} />
    </p>
  );
}
