"use client";

import { ButtonLink, Card, PageHeader } from "@zoiko/ui";

/** A screen the operator no longer needs: company and identity verification now run for every country with no operator release or activation. The records and the
 * screen's code are kept; only the way in is closed, so nothing is lost and it can be brought back. */
export function RetiredPage({ title, what }: { title: string; what: string }) {
  return (
    <>
      <PageHeader title={title} description="Not needed any more." breadcrumbs={[{ label: "Regulatory" }, { label: title }]} />
      <Card title="Nothing to do here">
        <div className="space-y-3 text-sm">
          <p>{what}</p>
          <p>
            Company and identity verification no longer wait for an operator to release or activate anything. A person verifies themselves and their company. When a case needs a human, it
            appears under <strong>Case reviews</strong>.
          </p>
          <ButtonLink href="/case-reviews" size="sm">
            Open Case reviews
          </ButtonLink>
        </div>
      </Card>
    </>
  );
}
