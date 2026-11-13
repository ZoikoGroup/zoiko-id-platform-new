import type { PublicPricingPage } from "@zoiko/api-client";
import { Banner, ButtonLink } from "@zoiko/ui";
import type { Metadata } from "next";

import { PARTNER_HERO, PARTNER_INTRO, PARTNER_TERMS, PARTNER_TYPES_COPY } from "@/features/pricing/copy";
import { PartnerInquiry } from "@/features/pricing/partner-inquiry";
import { server } from "@/server/bff";

export const metadata: Metadata = {
  title: "Partners",
  description: "Reseller, referral and embedded partner arrangements for Zoiko iD, agreed by contract and market by market.",
  alternates: { canonical: "/pricing/partners" },
};
export const dynamic = "force-dynamic";

async function loadPage(): Promise<PublicPricingPage | null> {
  const { env } = server();
  try {
    const res = await fetch(`${env.cellApiUrl.replace(/\/$/, "")}/v1/public/pricing/page`, { cache: "no-store", headers: { accept: "application/json" } });
    return res.ok ? ((await res.json()) as PublicPricingPage) : null;
  } catch {
    return null;
  }
}

export default async function Page() {
  const page = await loadPage();
  return (
    <div className="space-y-10">
      <section className="space-y-3" aria-labelledby="partner-h">
        <h1 id="partner-h" className="text-3xl font-bold tracking-tight text-ink sm:text-4xl">
          {PARTNER_HERO}
        </h1>
        <p className="max-w-3xl text-lg text-ink-muted">{PARTNER_INTRO}</p>
        <ButtonLink variant="secondary" href="/pricing">
          Back to plans and pricing
        </ButtonLink>
      </section>
      <section aria-labelledby="types-h" className="space-y-3">
        <h2 id="types-h" className="text-xl font-semibold">
          Kinds of partner
        </h2>
        <ul className="grid gap-4 md:grid-cols-3">
          {PARTNER_TYPES_COPY.map((t) => (
            <li key={t.code} className="rounded-control border border-border p-4 text-sm">
              <p className="font-semibold">{t.label}</p>
              <p className="mt-1 text-ink-muted">{t.text}</p>
            </li>
          ))}
        </ul>
      </section>
      <section aria-labelledby="terms-h" className="space-y-3">
        <h2 id="terms-h" className="text-xl font-semibold">
          How an arrangement works
        </h2>
        <dl className="grid gap-4 md:grid-cols-2">
          {PARTNER_TERMS.map((t) => (
            <div key={t.label} className="rounded-control border border-border p-4 text-sm">
              <dt className="font-semibold">{t.label}</dt>
              <dd className="mt-1 text-ink-muted">{t.text}</dd>
            </div>
          ))}
        </dl>
      </section>
      <section id="inquiry" aria-labelledby="inquiry-h" className="space-y-3">
        <h2 id="inquiry-h" className="sr-only">
          Partner inquiry
        </h2>
        {page ? <PartnerInquiry notice={page.notice} /> : <Banner tone="warn">The inquiry form is not available right now. Please try again shortly.</Banner>}
      </section>
    </div>
  );
}
