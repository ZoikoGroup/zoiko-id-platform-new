import type { Metadata } from "next";

import { CaseReviewsPage } from "@/features/case-reviews";

export const metadata: Metadata = { title: "Case reviews" };

export default function Page() {
  return <CaseReviewsPage />;
}
