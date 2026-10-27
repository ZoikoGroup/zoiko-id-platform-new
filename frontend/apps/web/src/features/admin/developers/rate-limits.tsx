"use client";

import { Banner, Card, KeyValues } from "@zoiko/ui";

import { QueryView, useCellQuery } from "@/lib/cell";

export function RateLimitsSection() {
  const limits = useCellQuery(["admin", "usage", "limits"], (api) => api.usage.limits(), { refetchInterval: 5_000 });
  return (
    <div className="space-y-4">
      <QueryView query={limits}>
        {(l) =>
          l.enforced ? (
            <Card title="Your request limit" description="Applies to all authenticated API calls from your company, across every client.">
              <KeyValues
                items={[
                  ["Limit", `${l.limit_per_minute.toLocaleString()} requests per minute`],
                  ["Used in this window", `${l.used_this_minute.toLocaleString()} (${Math.round((l.used_this_minute / l.limit_per_minute) * 100)}%)`],
                  ["Window resets in", l.used_this_minute > 0 ? `${l.resets_in_s} seconds` : "Nothing used"],
                ]}
              />
              <div
                role="progressbar"
                aria-label="Request limit used"
                aria-valuemin={0}
                aria-valuemax={l.limit_per_minute}
                aria-valuenow={l.used_this_minute}
                className="mt-4 h-2 w-full overflow-hidden rounded-full bg-neutral-bg"
              >
                <div
                  className={l.used_this_minute / l.limit_per_minute > 0.8 ? "h-full bg-warn-fg" : "h-full bg-brand-600"}
                  style={{ width: `${Math.min(100, (l.used_this_minute / l.limit_per_minute) * 100)}%` }}
                />
              </div>
            </Card>
          ) : (
            <Banner tone="info" title="No request limit is enforced here">
              This environment does not limit requests per company.
            </Banner>
          )
        }
      </QueryView>
      <Card title="When you go over" description="Handle limits in your integration instead of retrying in a loop.">
        <ul className="list-disc space-y-2 pl-5 text-sm text-ink-muted">
          <li>
            The API answers <code className="font-mono">429</code> with the code <code className="font-mono">RATE_LIMITED</code> and a{" "}
            <code className="font-mono">Retry-After</code> header in seconds.
          </li>
          <li>Wait that long, then retry once. Use the same idempotency key for any write so a retry never repeats the action.</li>
          <li>Webhook deliveries to you are paced separately and retried with backoff, so they are not affected by this limit.</li>
          <li>The limit is set by the platform and cannot be raised from the console. Contact support if your workload needs more.</li>
        </ul>
      </Card>
    </div>
  );
}
