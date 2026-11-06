"use client";

import type { BulkVerificationResult } from "@zoiko/api-client";
import { Banner, Button, Card, Select, TextField } from "@zoiko/ui";
import { useState } from "react";

import { ApiErrorView, QueryView, useCellMutation, useCellQuery } from "@/lib/cell";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
export const MAX_PEOPLE = 50;

/** The principal ids typed or pasted (lines, commas or spaces), de-duplicated, and what is wrong with them. */
export function parsePeople(text: string): { ids: string[]; invalid: string[]; tooMany: boolean } {
  const parts = text.split(/[\s,;]+/).map((p) => p.trim()).filter(Boolean);
  const ids = [...new Set(parts.filter((p) => UUID.test(p)).map((p) => p.toLowerCase()))];
  return { ids, invalid: [...new Set(parts.filter((p) => !UUID.test(p)))], tooMany: ids.length > MAX_PEOPLE };
}

export function BulkStartSection() {
  const profiles = useCellQuery(["admin", "verification", "profiles"], (api) => api.verification.profiles());
  const [profile, setProfile] = useState("");
  const [text, setText] = useState("");
  const [purpose, setPurpose] = useState("onboarding");
  const [country, setCountry] = useState("GB");
  const [result, setResult] = useState<BulkVerificationResult | null>(null);
  const people = parsePeople(text);
  const valid = !!profile && people.ids.length > 0 && people.invalid.length === 0 && !people.tooMany && purpose.trim().length > 0 && /^[A-Za-z]{2}$/.test(country);
  const run = useCellMutation(
    (api) => api.verification.startBulk({ profile_id: profile, subject_principal_ids: people.ids, purpose: purpose.trim(), jurisdiction: country.toUpperCase() }),
    { onSuccess: (r) => setResult(r), success: "Verifications started" },
  );
  return (
    <Card title="Start verification for many people" description={`Up to ${MAX_PEOPLE} people at once, with one profile. Part of the bulk verification plan feature.`}>
      <div className="space-y-4">
        <QueryView query={profiles}>
          {(d) => (
            <Select
              label="Profile"
              value={profile}
              onChange={setProfile}
              options={[{ id: "", label: "Choose a profile" }, ...d.items.filter((p) => p.status === "ACTIVE").map((p) => ({ id: p.profile_id, label: `${p.name} (${p.level})` }))]}
            />
          )}
        </QueryView>
        <TextField
          label="People (principal IDs)"
          multiline
          rows={5}
          mono
          value={text}
          onChange={setText}
          description={`${people.ids.length} of ${MAX_PEOPLE}. One per line.`}
          errorMessage={people.invalid.length ? `Not an ID: ${people.invalid.slice(0, 3).join(", ")}` : people.tooMany ? `At most ${MAX_PEOPLE}.` : undefined}
        />
        <div className="grid grid-cols-2 gap-3">
          <TextField label="Purpose" value={purpose} onChange={setPurpose} />
          <TextField label="Country" value={country} onChange={setCountry} description="Two letters, e.g. GB." />
        </div>
        <Button pending={run.isPending} isDisabled={!valid} onPress={() => run.mutate(undefined)}>
          Start {people.ids.length || ""} verification{people.ids.length === 1 ? "" : "s"}
        </Button>
        {run.error ? <ApiErrorView error={run.error} /> : null}
        {result ? (
          <div className="space-y-2">
            <Banner tone={result.failed ? "warn" : "ok"} title={`${result.started} of ${result.requested} started`}>
              {result.failed ? "The ones that did not start are listed below with the reason; the others went ahead." : "Each person gets their own link from the provider."}
            </Banner>
            <ul className="divide-y divide-border rounded-md border border-border text-sm">
              {result.items.map((i) => (
                <li key={i.subject_principal_id} className="flex justify-between gap-3 px-3 py-2">
                  <span className="font-mono text-xs">{i.subject_principal_id}</span>
                  <span className={i.started ? "text-ok-text" : "text-danger-text"}>{i.started ? (i.status ?? "Started") : (i.code ?? "Not started")}</span>
                </li>
              ))}
            </ul>
          </div>
        ) : null}
      </div>
    </Card>
  );
}
