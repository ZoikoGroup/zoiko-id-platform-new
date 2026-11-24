"use client";

import { useRegion } from "./region";

/** A company's region as a place name ("United Kingdom (London)"). A dash until the catalogue has loaded, so a raw cell id is never shown. */
export function RegionText({ cellId }: { cellId: string | null | undefined }) {
  const region = useRegion(cellId);
  return <span>{region ?? "—"}</span>;
}
