"use client";

import { Badge, Card } from "@zoiko/ui";

import { ApiErrorView, useCellQuery } from "@/lib/cell";

import { waitingLine } from "./logic";

/** The cases waiting for a reviewer, oldest first, for a person who may review them: never their own case and not one they already signed. A market's deadline shows when a case is overdue. */
export function ReviewQueue() {
  const q = useCellQuery(["idv-cases", "queue"], (api) => api.idv.reviewQueue(), { refetchInterval: 60000 });
  if (q.isError) return <ApiErrorView error={q.error} />;
  const d = q.data;
  if (!d) return null;
  return (
    <Card title="Waiting for a reviewer" description="Oldest first. These are cases people decide: each needs two different reviewers. Your own case, and any you already signed, are not listed.">
      {d.items.length === 0 ? (
        <p className="text-sm text-muted">Nothing is waiting for you.</p>
      ) : (
        <ul className="space-y-1 text-sm">
          {d.items.map((i) => (
            <li key={i.case_id}>
              <Badge tone={i.overdue ? "danger" : "info"}>{i.overdue ? "overdue" : "waiting"}</Badge> {i.market} · {i.signatures} of 2 signed · {i.has_photo ? "photograph held" : "no photograph"} · {waitingLine(i)}
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
