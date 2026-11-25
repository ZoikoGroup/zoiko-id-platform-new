"use client";

import { Banner, Card, DateTime, Select } from "@zoiko/ui";
import { CheckCircle2, Circle } from "lucide-react";
import { useState } from "react";

import { QueryView, useCellQuery } from "@/lib/cell";

import { checklistHeadline, errorGuidance, formatDuration, sdkAction, sdkProblem, sdkStateWords } from "./developer-logic";

/** The five steps from a new credential to a signed webhook your server confirmed. Ready means confirmed, not "a key was made". */
export function DeveloperHomeSection() {
  const apps = useCellQuery(["developers", "applications"], (api) => api.verifyApi.applications());
  const [id, setId] = useState("");
  const list = apps.data?.items ?? [];
  const chosen = id || list[0]?.application_id || "";
  const q = useCellQuery(["developers", "onboarding", chosen], (api) => api.verifyApi.onboarding(chosen), { enabled: chosen !== "" });
  return (
    <Card title="Your first verification" description="Five steps, each backed by something that really happened. The last one is your own server confirming it received a signed webhook and checked the signature.">
      <div className="space-y-4">
        <QueryView query={apps} loadingRows={1}>
          {(d) => (
            <Select label="Application" value={chosen} onChange={setId} options={d.items.map((a) => ({ id: a.application_id, label: `${a.name} (${a.environment.toLowerCase()})` }))} />
          )}
        </QueryView>
        {chosen ? (
          <QueryView query={q} loadingRows={4}>
            {(c) => {
              const h = checklistHeadline(c);
              return (
                <div className="space-y-3">
                  <Banner tone={h.tone} title={c.ready ? "Working in the sandbox" : "Not ready yet"}>
                    {h.text}
                  </Banner>
                  <ol className="space-y-2">
                    {c.steps.map((s) => (
                      <li key={s.step} className="flex items-start gap-2 text-sm">
                        {s.done ? <CheckCircle2 aria-label="done" className="mt-0.5 size-4 text-ok" /> : <Circle aria-label="not done" className="mt-0.5 size-4 text-ink-muted" />}
                        <span>
                          {s.title}
                          {s.at ? (
                            <span className="text-ink-muted">
                              {" "}
                              (<DateTime value={s.at} />)
                            </span>
                          ) : null}
                        </span>
                      </li>
                    ))}
                  </ol>
                  <p className="text-sm text-ink-muted">Time to your first verified webhook: {formatDuration(c.seconds_to_first_verified_webhook)}. {c.note}</p>
                </div>
              );
            }}
          </QueryView>
        ) : null}
      </div>
    </Card>
  );
}

/** What the SDKs in the repository really are today. A package earns a download button only with a signed release and a current test run. */
export function SdkRegisterSection() {
  const q = useCellQuery(["developers", "sdks"], (api) => api.verifyApi.sdkRegister());
  return (
    <Card title="SDKs" description="What each package is today. This lists what has been proved, not what is planned.">
      <QueryView query={q} loadingRows={3}>
        {(r) => (
          <div className="space-y-3">
            <Banner tone={r.ga_count ? "info" : "warn"} title={r.statement}>
              {r.note}
            </Banner>
            <ul className="space-y-3">
              {r.items.map((s) => (
                <li key={s.id} className="rounded border border-line p-3 text-sm">
                  <p className="font-medium">
                    {s.name} <span className="text-ink-muted">({s.language})</span>
                  </p>
                  <p className="text-ink-muted">{sdkStateWords(s)}. {s.purpose}</p>
                  {s.problems.length ? <ul className="list-disc pl-5 text-xs text-ink-muted">{s.problems.map((p) => <li key={p}>{sdkProblem(p)}</li>)}</ul> : null}
                  <p className="mt-1 text-xs">{sdkAction(s)}: <span className="font-mono">{s.id}</span></p>
                </li>
              ))}
            </ul>
          </div>
        )}
      </QueryView>
    </Card>
  );
}

/** The error classes a server can meet and what to do about each. */
export function ErrorCatalogSection() {
  const q = useCellQuery(["developers", "errors"], (api) => api.verifyApi.errorCatalog());
  return (
    <Card title="Errors" description="Every error your server can meet, and what to do about it.">
      <QueryView query={q} loadingRows={4}>
        {(c) => (
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="text-ink-muted">
                <th className="py-1 pr-3">Code</th>
                <th className="pr-3">HTTP</th>
                <th className="pr-3">What it means</th>
                <th>What to do</th>
              </tr>
            </thead>
            <tbody>
              {c.errors.map((e) => (
                <tr key={e.code} className="border-t border-line align-top">
                  <td className="py-1 pr-3 font-mono text-xs">{e.code}</td>
                  <td className="pr-3">{e.http_status}</td>
                  <td className="pr-3">{e.meaning}</td>
                  <td>{errorGuidance(e)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </QueryView>
    </Card>
  );
}
