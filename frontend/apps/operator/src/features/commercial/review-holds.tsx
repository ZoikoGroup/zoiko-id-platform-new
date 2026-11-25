"use client";

import { Badge, Banner, Button, Card, EmptyState, TextField } from "@zoiko/ui";
import { ShieldAlert } from "lucide-react";
import { useState } from "react";

import { ApiErrorView, useCellMutation, useCellQuery } from "@/lib/op";

import { COMMERCIAL_KEY } from "./keys";

const KEY = [...COMMERCIAL_KEY, "review-holds"];

/** A hold needs a reason code, a release needs a note the company can read. */
export const holdReady = (code: string): boolean => /^[A-Z][A-Z0-9_]{2,39}$/.test(code.trim());
export const releaseReady = (note: string): boolean => note.trim().length >= 3;

/** Regulatory holds on Verify API review cases (CR-167). While a case is held nobody can decide it. One operator places the hold; a DIFFERENT operator releases it. */
export function ReviewHoldsView() {
  const list = useCellQuery(KEY, (api) => api.commercial.reviewCasesOpen());
  return (
    <Card title="Review case holds" description="Open review cases across partners. A held case cannot be decided. The person who places a hold cannot release it.">
      {list.isPending ? (
        <p className="text-sm text-ink-muted">Loading…</p>
      ) : list.error ? (
        <ApiErrorView error={list.error} title="The cases could not be loaded" onRetry={() => void list.refetch()} />
      ) : list.data.items.length === 0 ? (
        <EmptyState title="Nothing open" icon={ShieldAlert} compact>
          No partner has a review case open.
        </EmptyState>
      ) : (
        <div className="space-y-3">
          {list.data.items.map((c) => (
            <HoldPanel key={c.case_id} id={c.case_id} tenant={c.tenant_id} session={c.session_id} held={c.on_hold} />
          ))}
        </div>
      )}
    </Card>
  );
}

function HoldPanel({ id, tenant, session, held }: { id: string; tenant: string; session: string; held: boolean }) {
  const [text, setText] = useState("");
  const opts = { invalidate: [KEY] };
  const hold = useCellMutation<void, unknown>((api) => api.commercial.reviewHold(id, text), { ...opts, success: "Held. Nobody can decide this case." });
  const release = useCellMutation<void, unknown>((api) => api.commercial.reviewReleaseHold(id, text), { ...opts, success: "Released." });
  const error = hold.error ?? release.error;
  return (
    <section aria-label={`Case ${id}`} className="space-y-2 rounded-lg border border-border p-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-mono text-xs">{tenant}</span>
        <span className="font-mono text-xs">{session}</span>
        {held ? <Badge tone="danger">On hold</Badge> : null}
      </div>
      {held ? (
        <>
          <Banner tone="warn" title="Releasing lets the case be decided again" />
          <TextField label="Note (recorded)" value={text} onChange={setText} />
          <Button size="sm" isDisabled={!releaseReady(text)} pending={release.isPending} onPress={() => release.mutate(undefined)}>
            Release hold
          </Button>
        </>
      ) : (
        <>
          <TextField label="Reason code" value={text} onChange={setText} description="Capital letters, for example REGULATOR_REQUEST." />
          <Button size="sm" variant="secondary" isDisabled={!holdReady(text)} pending={hold.isPending} onPress={() => hold.mutate(undefined)}>
            Place hold
          </Button>
        </>
      )}
      {error ? <ApiErrorView error={error} title="That was not recorded" onRetry={() => (hold.reset(), release.reset())} /> : null}
    </section>
  );
}
