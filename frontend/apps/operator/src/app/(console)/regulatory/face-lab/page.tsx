import type { Metadata } from "next";

import { FaceLabPage } from "@/features/regulatory/face-lab";

export const metadata: Metadata = { title: "Face lab" };

export default function Page() {
  return <FaceLabPage />;
}
