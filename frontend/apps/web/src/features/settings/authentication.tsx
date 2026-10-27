"use client";

import type { AuthenticationSettings } from "@zoiko/api-client";
import { Banner, Button, Card, Select } from "@zoiko/ui";
import { useState } from "react";

import { ApiErrorView, QueryView, useCellMutation, useCellQuery } from "@/lib/cell";

import { ConflictBanner, isConflict, SETTINGS_KEY, SettingsHistory } from "./shared";

const DEFAULT = "default";
const HOURS = [1, 2, 4, 8, 12];
const MINUTES = [5, 10, 15, 30];

const hoursOptions = [{ id: DEFAULT, label: "Platform default (12 hours)" }, ...HOURS.map((h) => ({ id: String(h), label: `${h} hour${h === 1 ? "" : "s"}` }))];
const minutesOptions = [{ id: DEFAULT, label: "Platform default (30 minutes)" }, ...MINUTES.map((m) => ({ id: String(m), label: `${m} minutes` }))];

export function AuthenticationSettings() {
  const all = useCellQuery(SETTINGS_KEY, (api) => api.settings.all());
  return (
    <div className="space-y-4">
      <QueryView query={all}>
        {(data) => (
          <Form
            key={data.areas.AUTHENTICATION.revision}
            revision={data.areas.AUTHENTICATION.revision}
            value={data.areas.AUTHENTICATION.value}
            reload={() => void all.refetch()}
          />
        )}
      </QueryView>
      <SettingsHistory area="AUTHENTICATION" />
    </div>
  );
}

function Form({ revision, value, reload }: { revision: number; value: AuthenticationSettings; reload: () => void }) {
  const [hours, setHours] = useState(value.session_absolute_hours ? String(value.session_absolute_hours) : DEFAULT);
  const [idle, setIdle] = useState(value.session_idle_minutes ? String(value.session_idle_minutes) : DEFAULT);

  const save = useCellMutation(
    (api, v: AuthenticationSettings) => api.settings.put<AuthenticationSettings>("AUTHENTICATION", revision, v),
    { invalidate: [SETTINGS_KEY], success: "Session limits saved. They apply to every request from now on." },
  );
  const body: AuthenticationSettings = {
    session_absolute_hours: hours === DEFAULT ? null : Number(hours),
    session_idle_minutes: idle === DEFAULT ? null : Number(idle),
  };
  const dirty = JSON.stringify(body) !== JSON.stringify(value);

  return (
    <Card
      title="Sessions"
      description={`Revision ${revision}${revision === 0 ? " (platform defaults)" : ""}. Your company can make sign-in sessions shorter than the platform's, never longer.`}
    >
      <div className="space-y-4">
        <div className="grid gap-4 sm:grid-cols-2">
          <Select label="Maximum session length" options={hoursOptions} value={hours} onChange={setHours} description="After this, everyone signs in again." />
          <Select label="Idle timeout" options={minutesOptions} value={idle} onChange={setIdle} description="Signed out after this long without activity." />
        </div>
        <Banner tone="info" title="Takes effect immediately">
          Sessions already open are held to the new limits at their next request, so shortening this signs out anyone who has been signed in or idle for longer.
        </Banner>
        {isConflict(save.error) ? <ConflictBanner onReload={() => { save.reset(); reload(); }} /> : save.error ? <ApiErrorView error={save.error} /> : null}
        <Button isDisabled={!dirty} pending={save.isPending} onPress={() => save.mutate(body)}>
          Save session limits
        </Button>
      </div>
    </Card>
  );
}
