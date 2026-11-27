"use client";

import type { VerifyApiMedia, VerifyApiMediaOpened } from "@zoiko/api-client";
import { Badge, Banner, Button, TextField } from "@zoiko/ui";
import { useState } from "react";

import { ApiErrorView, QueryView, useCellMutation, useCellQuery } from "@/lib/cell";

const when = (iso: string | null | undefined): string => (iso ? new Date(iso).toLocaleString("en-GB") : "-");
const KIND: Record<string, string> = { DOCUMENT: "Document photograph", SELFIE: "Selfie" };

/** The photographs kept for one verification, when the deployment keeps them. Listing them is free; opening one needs a reason, which is recorded with who and when before the image comes back.
 * The image lives only in this component's state: close the dialog, or press Hide, and it is gone from the page. */
export function SessionPhotographs({ sessionId }: { sessionId: string }) {
  const q = useCellQuery(["verify-api", "media", sessionId], (api) => api.verifyApi.sessionMedia(sessionId));
  const [reason, setReason] = useState("");
  const [shown, setShown] = useState<VerifyApiMediaOpened | null>(null);
  const open = useCellMutation((api, m: VerifyApiMedia) => api.verifyApi.openMedia(sessionId, m.media_id, reason.trim()), {
    invalidate: [["verify-api", "media", sessionId] as never],
    onSuccess: (r) => setShown(r as VerifyApiMediaOpened),
  });
  const reasonOk = reason.trim().length >= 5;
  return (
    <section aria-label="Photographs" className="space-y-2">
      <h3 className="text-sm font-semibold text-ink">Photographs</h3>
      <QueryView query={q} loadingRows={1}>
        {(d) => (
          <div className="space-y-3">
            {d.items.length === 0 ? (
              <p className="text-sm text-ink-muted">
                {d.keeping_days > 0
                  ? "No photographs were kept for this verification. Photographs are kept only for checks made after this was turned on."
                  : "No photographs are kept. They are compared and discarded, and only the result is recorded."}
              </p>
            ) : (
              <>
                <p className="text-xs text-ink-muted">
                  Kept encrypted for {d.keeping_days} days, then deleted. Say why you are opening one: the reason, who you are and the time are recorded before it is shown.
                </p>
                <TextField label="Why are you opening a photograph?" value={reason} onChange={setReason} description="At least 5 characters. For example: customer disputes the result, ticket 4821." />
                <ul className="space-y-1 text-sm">
                  {d.items.map((m) => (
                    <li key={m.media_id} className="flex flex-wrap items-center gap-2">
                      <span className="w-40">
                        Attempt {m.attempt} · {KIND[m.kind] ?? m.kind}
                      </span>
                      <Badge tone={m.available ? "ok" : "neutral"}>{m.available ? "Kept" : "Deleted"}</Badge>
                      <span className="text-xs text-ink-muted">taken {when(m.captured_at)} · deleted {when(m.expires_at)}</span>
                      <Button size="xs" variant="secondary" isDisabled={!m.available || !reasonOk || open.isPending} onPress={() => open.mutate(m)}>
                        View
                      </Button>
                    </li>
                  ))}
                </ul>
              </>
            )}
            {open.error ? <ApiErrorView error={open.error} title="The photograph was not opened" onRetry={() => open.reset()} /> : null}
            {shown ? (
              <Banner tone="warn" title={`${KIND[shown.kind] ?? shown.kind}, attempt ${shown.attempt}`}>
                <img src={`data:${shown.content_type};base64,${shown.image_base64}`} alt={`${KIND[shown.kind] ?? shown.kind}, attempt ${shown.attempt}`} className="max-h-72 rounded border" />
                <div className="mt-2">
                  <Button size="xs" variant="secondary" onPress={() => setShown(null)}>
                    Hide
                  </Button>
                </div>
              </Banner>
            ) : null}
            {d.opened.length ? (
              <div className="space-y-1">
                <p className="text-xs font-medium text-ink">Every time a photograph was opened</p>
                <ul className="space-y-0.5 text-xs text-ink-muted">
                  {d.opened.map((a) => (
                    <li key={a.access_id}>
                      {when(a.opened_at)} · {a.reason}
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}
          </div>
        )}
      </QueryView>
    </section>
  );
}
