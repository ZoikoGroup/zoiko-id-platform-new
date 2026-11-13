import type { Metadata } from "next";

import { OfferingsPage } from "@/features/regulatory/offerings";

export const metadata: Metadata = { title: "Service offerings" };

export default function Page() {
  return <OfferingsPage />;
}
