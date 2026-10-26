import type { Metadata } from "next";

import { CellsPage } from "@/features/cells";

export const metadata: Metadata = { title: "Cells" };

export default function Page() {
  return <CellsPage />;
}
