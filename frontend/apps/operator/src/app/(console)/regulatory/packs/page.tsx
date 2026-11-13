import type { Metadata } from "next";

import { PacksPage } from "@/features/regulatory/packs";

export const metadata: Metadata = { title: "Policy packs" };

export default function Page() {
  return <PacksPage />;
}
