import type { Metadata } from "next";

import { TrustRoomPage } from "@/features/trust-room/room";

export const metadata: Metadata = { title: "Security documents" };

export default function Page() {
  return <TrustRoomPage />;
}
