"use client";

import { Badge, Banner, PageHeader, Tabs } from "@zoiko/ui";
import { ScanFace, ShieldCheck } from "lucide-react";

import { useDirectMode } from "@/features/commercial/mode";
import { ReviewHoldsView } from "@/features/commercial/review-holds";
import { VerifyCasesView } from "@/features/commercial/verify-cases";
import { VerifyGoLiveView } from "@/features/commercial/verify-go-live";
import { VerifyPricesView } from "@/features/commercial/verify-prices";

/** Everything an operator does for the identity verification product, in one place and in the order it comes up: who asked for production, the disputes and appeals people raise, the reviews that are on hold,
 * and what a verification costs. These four used to sit among fourteen tabs on the Commercial page, with nothing to say they belonged together. */
export function IdentityOpsPage() {
  const direct = useDirectMode();
  return (
    <>
      <PageHeader
        icon={ScanFace}
        title="Identity verification"
        description="The operator side of the product companies use to verify their own customers. Companies open production themselves once their identity, their company and a paid plan are complete, so this page is for what still needs a person."
        actions={
          direct ? (
            <Badge tone="info" size="sm">
              <span className="inline-flex items-center gap-1.5">
                <ShieldCheck aria-hidden className="size-3.5" />
                Single operator: every change is logged under your name
              </span>
            </Badge>
          ) : undefined
        }
      />
      <Banner tone="info" className="mb-5">
        Photographs of a person are never shown here. A company's own team opens them, with a reason, and each opening is recorded.
      </Banner>
      <Tabs
        label="Identity verification"
        tabs={[
          { id: "go-live", label: "Production access", content: <VerifyGoLiveView /> },
          { id: "cases", label: "Disputes and appeals", content: <VerifyCasesView /> },
          { id: "holds", label: "Reviews on hold", content: <ReviewHoldsView /> },
          { id: "prices", label: "What a verification costs", content: <VerifyPricesView /> },
        ]}
      />
    </>
  );
}
