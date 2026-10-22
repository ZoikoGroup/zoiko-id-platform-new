import type { Metadata } from "next";

import { MandatesPage } from "@/features/admin/mandates/mandates-page";

export const metadata: Metadata = { title: "Mandates" };

export default function Page() {
  return <MandatesPage />;
}
