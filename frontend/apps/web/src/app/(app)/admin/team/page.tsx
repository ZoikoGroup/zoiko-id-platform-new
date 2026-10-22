import type { Metadata } from "next";

import { TeamPage } from "@/features/admin/team/team-page";

export const metadata: Metadata = { title: "Team & invitations" };

export default function Page() {
  return <TeamPage />;
}
