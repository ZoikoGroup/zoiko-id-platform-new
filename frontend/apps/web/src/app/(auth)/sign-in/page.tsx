import type { Metadata } from "next";

import { SignIn } from "@/features/sign-in/sign-in";
import { headers } from "next/headers";

import { signInConnections, workspaceName } from "@/server/workspace";

export const metadata: Metadata = { title: "Sign in" };

export default async function SignInPage({ searchParams }: { searchParams: Promise<Record<string, string | undefined>> }) {
  const sp = await searchParams;
  const cookie = (await headers()).get("cookie");
  const workspace = workspaceName(cookie);
  const connections = await signInConnections(cookie);
  return (
    <SignIn
      returnTo={sp.returnTo}
      error={sp.error}
      reason={sp.reason}
      connections={connections}
      workspace={workspace}
    />
  );
}
