import type { Invoice } from "@zoiko/api-client";

/** The states the billing wireframe (B03) lists, worked out from what the cell sent. Nothing is invented: an invoice the cell says nothing
 * more about is Open, and a status the cell sends that this does not know is shown as Pending verification rather than as paid. */
export type InvoiceState = "DRAFT" | "OPEN" | "PARTIAL" | "OVERDUE" | "PAID" | "CREDITED" | "VOID" | "PENDING_VERIFICATION";

export const INVOICE_STATE_LABEL: Record<InvoiceState, string> = {
  DRAFT: "Draft",
  OPEN: "Open",
  PARTIAL: "Partly paid",
  OVERDUE: "Overdue",
  PAID: "Paid",
  CREDITED: "Credited",
  VOID: "Void",
  PENDING_VERIFICATION: "Pending verification",
};

export function invoiceState(i: Invoice, now: Date = new Date()): InvoiceState {
  const status = (i.status ?? "").toUpperCase();
  if (status === "VOID" || status === "VOIDED") return "VOID";
  if (status === "DRAFT") return "DRAFT";
  if (status.includes("PENDING")) return "PENDING_VERIFICATION";
  const gross = Number(i.gross);
  const credited = Number(i.credited ?? 0);
  const paid = Number(i.paid ?? 0);
  const outstanding = i.outstanding === null ? null : Number(i.outstanding);
  if (outstanding === null || !Number.isFinite(outstanding) || !Number.isFinite(gross)) return "PENDING_VERIFICATION";
  if (outstanding <= 0) return credited > 0 && credited >= gross ? "CREDITED" : "PAID";
  if (i.due_at && new Date(i.due_at).getTime() < now.getTime()) return "OVERDUE";
  return paid > 0 ? "PARTIAL" : "OPEN";
}

export type InvoiceFilter = { text: string; state: string; currency: string; from: string; to: string };
export const NO_FILTER: InvoiceFilter = { text: "", state: "", currency: "", from: "", to: "" };

/** Search by invoice number or period, narrowed by state, currency and issue date (YYYY-MM-DD, inclusive). */
export function filterInvoices(items: Invoice[], f: InvoiceFilter, now: Date = new Date()): Invoice[] {
  const text = f.text.trim().toLowerCase();
  return items.filter((i) => {
    if (text && !`${i.number ?? ""} ${i.period}`.toLowerCase().includes(text)) return false;
    if (f.state && invoiceState(i, now) !== f.state) return false;
    if (f.currency && i.currency !== f.currency) return false;
    const day = (i.issued_at ?? "").slice(0, 10);
    if (f.from && (!day || day < f.from)) return false;
    if (f.to && (!day || day > f.to)) return false;
    return true;
  });
}

/** A payable balance: something is owed and the invoice is issued, not void, draft or still being verified. */
export function isPayable(i: Invoice, now: Date = new Date()): boolean {
  const s = invoiceState(i, now);
  return s === "OPEN" || s === "PARTIAL" || s === "OVERDUE";
}
