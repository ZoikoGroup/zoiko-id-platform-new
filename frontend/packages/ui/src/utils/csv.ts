/** CSV export helpers (ZID-UX-001 §25, UX-26): neutralize spreadsheet formula injection and use
 * a UTF-8 BOM so locale spreadsheet apps decode correctly. Callers must also record the export as
 * an audit event through the backend; this helper only produces the bytes. */

const FORMULA_START = /^[=+\-@\t\r]/;

export function csvCell(value: unknown): string {
  let s = value === null || value === undefined ? "" : String(value);
  if (FORMULA_START.test(s)) s = `'${s}`;
  if (/[",\n\r]/.test(s)) s = `"${s.replace(/"/g, '""')}"`;
  return s;
}

export function toCsv(header: string[], rows: unknown[][]): string {
  return "﻿" + [header, ...rows].map((r) => r.map(csvCell).join(",")).join("\r\n");
}
