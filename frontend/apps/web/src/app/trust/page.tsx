import type { Metadata } from "next";

import { ClaimsView } from "@/features/trust-center/views";
import type { ClaimsFeed } from "@/features/trust-center/model";
import { trustFeed } from "@/server/trust";

export const dynamic = "force-dynamic";
export const metadata: Metadata = { title: "Trust Center", description: "What Zoiko iD says publicly about security, privacy and reliability, and the evidence behind each statement." };

export default async function Page() {
  const feed = await trustFeed<ClaimsFeed>("/public/trust/v1/claims");
  return (
    <article className="space-y-8">
      <div className="space-y-2">
        <h1 className="text-3xl font-bold tracking-tight text-ink">Trust Center</h1>
        <p className="text-ink-muted">Every statement here is approved by two reviewers and rests on evidence that is current today. If evidence lapses, the statement leaves this page.</p>
      </div>
      <ClaimsView feed={feed} />
      <section aria-labelledby="more" className="space-y-2">
        <h2 id="more" className="text-xl font-semibold text-ink">Where to look next</h2>
        <ul className="list-disc pl-5 text-ink">
          <li><a className="text-brand-700 underline" href="/trust/certifications">Assessments and reports</a>, with their exact scope.</li>
          <li><a className="text-brand-700 underline" href="/trust/privacy">Privacy, subprocessors and retention</a>, by market.</li>
          <li><a className="text-brand-700 underline" href="/trust/status">Service status</a> and how availability is measured.</li>
          <li><a className="text-brand-700 underline" href="/trust/security">Report a vulnerability</a>.</li>
        </ul>
      </section>
    </article>
  );
}
