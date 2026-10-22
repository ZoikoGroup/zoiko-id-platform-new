import type { Metadata } from "next";

import { ResidencyPage } from "@/features/admin/residency/residency-page";

export const metadata: Metadata = { title: "Residency & sovereignty" };

export default function Page() {
  return <ResidencyPage />;
}
