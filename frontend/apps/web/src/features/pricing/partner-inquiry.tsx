"use client";

import type { PublicPricingPage } from "@zoiko/api-client";

import { LeadForm } from "./lead-form";

/** The partner inquiry: the same form as the enterprise one, fixed to a partner arrangement. It creates no account, quote, contract or payment. */
export function PartnerInquiry({ notice }: { notice: PublicPricingPage["notice"] }) {
  return <LeadForm capabilities={[]} names={{}} kind="PARTNER" onKind={() => undefined} notice={notice} lockKind />;
}
