"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";

import { useCellQuery } from "@/lib/op";

/** Signed in: a single operator lands on Commercial (the work), a governed deployment on My access (the grants). Until the cell says which, nothing moves. */
export function HomeRedirect() {
  const router = useRouter();
  const caps = useCellQuery(["operator", "capabilities"], (api) => api.capabilities());
  const mode = caps.data?.mode;
  useEffect(() => {
    if (mode) router.replace(mode === "direct" ? "/identity-verification" : "/requests");
  }, [mode, router]);
  return (
    <div className="p-8 text-sm text-ink-muted" role="status">
      Opening your console...
    </div>
  );
}
