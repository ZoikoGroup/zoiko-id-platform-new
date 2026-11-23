import type { Metadata } from "next";

import { RetiredPage } from "@/features/regulatory/retired";

export const metadata: Metadata = { title: "Company verification packs" };

export default function Page() {
  return <RetiredPage title="Company verification packs" what="Per-country company verification packs were proposed, signed and activated here." />;
}
