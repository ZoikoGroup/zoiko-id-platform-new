/** Pure checks for the Trust Center operator forms (ZID-CP-WF12; CR-174). The cell validates everything again; these only keep a button from being offered for an obviously unfinished form. */

export function splitList(s: string): string[] {
  return s.split(/[,\n]+/).map((x) => x.trim()).filter(Boolean);
}

const HEX64 = /^[0-9a-f]{64}$/;

export function evidenceReady(f: { digest: string; scheme: string; issuer: string; legal_entity: string; holder: string; products: string; period_start: string; period_end: string }): boolean {
  return HEX64.test(f.digest) && f.scheme.trim().length >= 2 && f.issuer.trim().length >= 2 && f.legal_entity.trim().length >= 2 && f.holder.trim().length >= 2 && splitList(f.products).length > 0 && f.period_start !== "" && f.period_end !== "" && f.period_end >= f.period_start;
}

export function figureReady(f: { service: string; region: string; window_label: string; numerator: string; denominator: string; source_ref: string }): boolean {
  const good = Number(f.numerator);
  const all = Number(f.denominator);
  return /^[a-z][a-z0-9_-]{1,62}$/.test(f.service) && /^[a-z0-9-]{2,24}$/.test(f.region) && /^\d{4}-\d{2}(-\d{2})?$/.test(f.window_label) && f.numerator !== "" && f.denominator !== "" && Number.isInteger(good) && Number.isInteger(all) && all > 0 && good <= all && f.source_ref.trim().length >= 3;
}
