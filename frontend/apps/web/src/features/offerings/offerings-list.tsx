/** The public service-offerings list (Global Service Offerings, Nov 2026; CR-088). Shows only what GET /v1/offerings returns: an offering
 * appears with the countries where provider rights, country rules, privacy controls and independent tests are verified. Nothing here is a
 * certification claim. */

export type PublicOffering = { offering_id: string; title: string; wording: string; countries: string[] };

export const OFFERINGS_HERO = "Practical uses of Zoiko iD";
export const OFFERINGS_SUBHERO =
  "Verify people, organizations, qualifications and authority, and keep defensible evidence of every decision.";
export const DELIVERY_RULE =
  "An offering is listed in a country only when provider rights, country rules, privacy controls and independent tests are verified there. Design specifications are not production certification.";

export function OfferingsList({ items }: { items: PublicOffering[] }) {
  if (!items.length) {
    return (
      <p role="status" className="rounded-control border border-border p-4 text-sm text-ink-muted">
        No offering is published in any country yet. Each one is listed here as soon as it is verified market by market.
      </p>
    );
  }
  return (
    <ul className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3" aria-label="Service offerings">
      {items.map((o) => (
        <li key={o.offering_id} className="rounded-card border border-border p-5">
          <h2 className="text-base font-semibold text-ink">{o.title}</h2>
          <p className="mt-1 text-sm text-ink-muted">{o.wording}</p>
          <p className="mt-3 text-xs text-ink-subtle">
            Available in: <span className="font-mono">{o.countries.join(", ")}</span>
          </p>
        </li>
      ))}
    </ul>
  );
}
