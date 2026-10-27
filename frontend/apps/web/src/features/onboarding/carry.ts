/** Company registration details and their checks, shared by server pages (which read the URL) and
 * client components: plain functions with no client directive, safe on both sides. */

/* ------------------------------------------------- company details entered at registration */

/** The registration details travel from the register page to company verification in the URL, as
 * the enrollment link does: public register identifiers, never stored in the browser (UX-001 §29). */
export type CarriedOrganization = { companyNumber?: string; lei?: string; development?: boolean };

export function carryQuery(v: CarriedOrganization): string {
  const q = new URLSearchParams();
  if (v.companyNumber && companyNumberValid(v.companyNumber)) q.set("company_number", normalizeCompanyNumber(v.companyNumber));
  if (v.lei && leiValid(v.lei)) q.set("lei", v.lei.trim().toUpperCase());
  if (v.development) q.set("org", "dev");
  return q.toString();
}

export function carried(sp: Record<string, string | undefined>): CarriedOrganization {
  return {
    companyNumber: sp.company_number && companyNumberValid(sp.company_number) ? normalizeCompanyNumber(sp.company_number) : undefined,
    lei: sp.lei && leiValid(sp.lei) ? sp.lei.toUpperCase() : undefined,
    development: sp.org === "dev",
  };
}

/** UK Companies House: 8 characters - digits, or a two-letter prefix (SC, NI, OC...) and 6 digits. */
export function normalizeCompanyNumber(v: string): string {
  const s = v.replace(/\s+/g, "").toUpperCase();
  return /^\d{1,7}$/.test(s) ? s.padStart(8, "0") : s;
}

export function companyNumberValid(v: string): boolean {
  return /^(\d{8}|[A-Z]{2}\d{6})$/.test(normalizeCompanyNumber(v));
}

export function leiValid(v: string): boolean {
  return /^[A-Z0-9]{18}\d{2}$/.test(v.trim().toUpperCase());
}
