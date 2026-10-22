import type { Metadata } from "next";

import { ReviewsPage } from "@/features/reviews";

export const metadata: Metadata = { title: "Post-reviews" };

export default function Page() {
  return <ReviewsPage />;
}
