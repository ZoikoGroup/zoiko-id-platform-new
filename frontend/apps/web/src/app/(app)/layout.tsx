import { headers } from "next/headers";
import { redirect } from "next/navigation";
import type { ReactNode } from "react";

import { ConsoleShell } from "@/components/console-shell";
import { server } from "@/server/bff";

/** Signed-in area. The session check here only decides what to render; the cell authorizes every
 * call regardless of what this layout shows. */
export default async function AppLayout({ children }: { children: ReactNode }) {
  const { bff, env } = server();
  const h = await headers();
  const subject = await bff.currentSubject(h.get("cookie"));
  if (!subject) {
    const path = h.get("x-zid-path") ?? "/account";
    redirect(`/sign-in?returnTo=${encodeURIComponent(path)}`);
  }
  const environment = env.environment === "local" ? "development" : env.environment;
  return (
    <ConsoleShell subject={subject} environment={environment}>
      {children}
    </ConsoleShell>
  );
}
