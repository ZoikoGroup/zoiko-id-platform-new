import type { Metadata } from "next";

import { HoldsPage } from "@/features/regulatory/holds";

export const metadata: Metadata = { title: "Holds" };

export default function Page() {
  return <HoldsPage />;
}
