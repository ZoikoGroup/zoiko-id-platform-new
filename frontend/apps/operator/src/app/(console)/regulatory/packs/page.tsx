import type { Metadata } from "next";

import { RetiredPage } from "@/features/regulatory/retired";

export const metadata: Metadata = { title: "Policy packs" };

export default function Page() {
  return <RetiredPage title="Policy packs" what="Per-country policy packs were released and activated here." />;
}
