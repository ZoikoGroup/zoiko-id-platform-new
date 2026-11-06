"use client";

import { Banner, Button } from "@zoiko/ui";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

/** Keys already followed in this page session. Kept in memory only: nothing about the person is written to browser storage. */
const followed = new Set<string>();

/** A step that finished, and the next place to go. The person is taken there after a few seconds unless they stop it; "Continue now" goes at once. It does not
 * run again for the same `once` key until the page is reloaded, so a page that is shown again does not throw the person out of it. */
export function AutoContinue({ to, title, children, seconds = 4, once }: { to: string; title: string; children: React.ReactNode; seconds?: number; once?: string }) {
  const router = useRouter();
  const [left, setLeft] = useState(seconds);
  const [stopped, setStopped] = useState(() => (once ? followed.has(once) : false));

  const go = () => {
    if (once) followed.add(once);
    router.push(to);
  };

  useEffect(() => {
    if (stopped) return;
    if (left <= 0) {
      go();
      return;
    }
    const t = window.setTimeout(() => setLeft((n) => n - 1), 1000);
    return () => window.clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [left, stopped]);

  return (
    <Banner tone="ok" title={title}>
      <div className="space-y-2">
        <div>{children}</div>
        <p role="status" className="text-sm">
          {stopped ? "Automatic continue is off." : `Continuing in ${left} second${left === 1 ? "" : "s"}.`}
        </p>
        <div className="flex flex-wrap gap-2">
          <Button size="sm" onPress={go}>
            Continue now
          </Button>
          {!stopped ? (
            <Button size="sm" variant="secondary" onPress={() => setStopped(true)}>
              Stay on this page
            </Button>
          ) : null}
        </div>
      </div>
    </Banner>
  );
}
