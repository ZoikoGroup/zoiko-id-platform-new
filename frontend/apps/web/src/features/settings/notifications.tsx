"use client";

import type { NotificationKind, NotificationSettings } from "@zoiko/api-client";
import { Badge, Banner, Button, Card, Checkbox } from "@zoiko/ui";
import { Lock } from "lucide-react";
import { useState } from "react";

import { ApiErrorView, QueryView, useCellMutation, useCellQuery } from "@/lib/cell";

import { ConflictBanner, isConflict, SETTINGS_KEY, SettingsHistory } from "./shared";

export function NotificationSettingsPanel() {
  const all = useCellQuery(SETTINGS_KEY, (api) => api.settings.all());
  const kinds = useCellQuery([...SETTINGS_KEY, "kinds"], (api) => api.settings.notificationKinds());
  return (
    <div className="space-y-4">
      <QueryView query={all}>
        {(data) => (
          <QueryView query={kinds}>
            {(k) => (
              <Form
                key={data.areas.NOTIFICATIONS.revision}
                kinds={k.items}
                revision={data.areas.NOTIFICATIONS.revision}
                value={data.areas.NOTIFICATIONS.value}
                reload={() => void all.refetch()}
              />
            )}
          </QueryView>
        )}
      </QueryView>
      <SettingsHistory area="NOTIFICATIONS" />
    </div>
  );
}

function Form({ kinds, revision, value, reload }: { kinds: NotificationKind[]; revision: number; value: NotificationSettings; reload: () => void }) {
  const [muted, setMuted] = useState<string[]>(value.muted_kinds);
  const save = useCellMutation(
    (api, v: NotificationSettings) => api.settings.put<NotificationSettings>("NOTIFICATIONS", revision, v),
    { invalidate: [SETTINGS_KEY], success: "Notification settings saved" },
  );
  const body: NotificationSettings = { muted_kinds: [...muted].sort() };
  const dirty = JSON.stringify(body.muted_kinds) !== JSON.stringify([...value.muted_kinds].sort());
  const optional = kinds.filter((k) => !k.mandatory);
  const mandatory = kinds.filter((k) => k.mandatory);

  return (
    <Card
      title="Which alerts you receive"
      description={`Revision ${revision}. Turn off routine alerts you do not need. Critical and high-severity security notices cannot be turned off.`}
    >
      <div className="space-y-5">
        <fieldset className="space-y-2">
          <legend className="text-sm font-medium text-ink">Routine alerts</legend>
          {optional.map((k) => (
            <Checkbox
              key={k.kind}
              isSelected={!muted.includes(k.kind)}
              onChange={(on) => setMuted((l) => (on ? l.filter((x) => x !== k.kind) : [...l, k.kind]))}
            >
              <span className="flex flex-col">
                <span>{k.title}</span>
                <span className="font-mono text-2xs text-ink-subtle">{k.kind} · {k.severity.toLowerCase()}</span>
              </span>
            </Checkbox>
          ))}
        </fieldset>
        <div>
          <h3 className="mb-2 flex items-center gap-1.5 text-sm font-medium text-ink">
            <Lock aria-hidden className="size-3.5" /> Always on
          </h3>
          <ul className="grid gap-1 text-sm text-ink-muted sm:grid-cols-2">
            {mandatory.map((k) => (
              <li key={k.kind} className="flex items-center gap-2">
                <Badge size="xs" tone={k.severity === "CRITICAL" ? "danger" : "warn"}>{k.severity}</Badge>
                {k.title}
              </li>
            ))}
          </ul>
        </div>
        <Banner tone="info" title="In-app only for now">
          Alerts appear in your Alerts &amp; tasks inbox and the top bar. Email and chat delivery are not available yet.
        </Banner>
        {isConflict(save.error) ? <ConflictBanner onReload={() => { save.reset(); reload(); }} /> : save.error ? <ApiErrorView error={save.error} /> : null}
        <Button isDisabled={!dirty} pending={save.isPending} onPress={() => save.mutate(body)}>
          Save notification settings
        </Button>
      </div>
    </Card>
  );
}
