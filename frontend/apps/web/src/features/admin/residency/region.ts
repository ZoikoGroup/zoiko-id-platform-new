import type { CellView } from "@zoiko/api-client";

import { useCellQuery } from "@/lib/cell";

/** What a person reads for where a company's data is kept: a place name such as "United Kingdom (London)", never the cell id ("uk-1", "local-1").
 * The technical id stays on the Residency page's detail line and in the operator console. */
export type RegionCell = Pick<CellView, "cell_id" | "jurisdiction"> & { display_name?: string | null };

export const UNKNOWN_REGION = "Your region";

function countryName(code: string | null | undefined): string | null {
  if (!code || !/^[A-Za-z]{2}$/.test(code)) return null;
  try {
    return new Intl.DisplayNames(["en"], { type: "region" }).of(code.toUpperCase()) ?? null;
  } catch {
    return null;
  }
}

/** The cell's display name; else its country's name; else a neutral word. Never the id. */
export function regionName(cell: RegionCell | null | undefined): string {
  if (!cell) return UNKNOWN_REGION;
  const named = cell.display_name?.trim();
  if (named) return named;
  return countryName(cell.jurisdiction) ?? UNKNOWN_REGION;
}

/** The region of a company's home cell, looked up in the catalogue. */
export function regionOf(cells: readonly RegionCell[], homeCellId: string | null | undefined): string {
  return regionName(cells.find((c) => c.cell_id === homeCellId));
}

/** One entry per region: two cells that read the same ("uk-1" and the local "local-1") are one choice. The company's own cell wins, so what is saved is still its cell. */
export function oneEntryPerRegion<T extends RegionCell>(cells: readonly T[], ownCellId?: string | null): T[] {
  const byName = new Map<string, T>();
  for (const c of cells) {
    const name = regionName(c);
    const kept = byName.get(name);
    if (!kept || c.cell_id === ownCellId) byName.set(name, c);
  }
  return [...byName.values()];
}

/** The region to show for a company, or null until the catalogue has loaded (so the page never flashes a raw id). */
export function useRegion(homeCellId: string | null | undefined): string | null {
  const cells = useCellQuery(["residency", "cells"], (api) => api.residency.cells());
  if (!homeCellId || !cells.data) return null;
  return regionOf(cells.data.items, homeCellId);
}
