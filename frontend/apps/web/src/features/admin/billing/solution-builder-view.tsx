"use client";

import type { CapabilityDependencies, OfferCard, QuotePreview, SolutionDraft } from "@zoiko/api-client";
import { Badge, Banner, Button, Card, Checkbox, RadioCards, TextField } from "@zoiko/ui";
import { ShieldCheck } from "lucide-react";

import {
  PURPOSES,
  STEPS,
  STATE_LABEL,
  canAdvance,
  coverageGroups,
  dependents,
  expiryNote,
  marketErrors,
  missingDependencies,
  nextStep,
  stateTone,
  toggleFamily,
  type Draft,
  type StepId,
} from "./solution-builder";

export type BuilderProps = {
  catalogue: OfferCard[];
  deps: CapabilityDependencies | undefined;
  draft: Draft;
  onDraft: (d: Draft) => void;
  step: StepId;
  onStep: (s: StepId) => void;
  preview: QuotePreview | undefined;
  previewPending: boolean;
  previewError: string | null;
  saved: SolutionDraft | undefined;
  onSave: () => void;
  onRequest: () => void;
  saving: boolean;
  requesting: boolean;
  /** Whether this person may save and send a configuration (company administration). The server decides regardless. */
  mayRequest: boolean;
};

/** The B06 guided panel (ZID-COM-WF-2026-11 WF-C02): purpose, services, coverage and review. It shows no price, because none is signed: the only way forward
 * is a quote the buyer approves. It is presentational; the container below it talks to the cell. */
export function SolutionBuilderView(p: BuilderProps) {
  const index = STEPS.findIndex((s) => s.id === p.step);
  const gate = canAdvance(p.step, p.draft, p.deps);
  return (
    <div className="space-y-4">
      <Banner tone="info" icon={ShieldCheck} title="Plan what you need. This is a forecast, not a purchase.">
        A configuration is saved as a draft and is never an entitlement. No price is shown or assumed: a binding price needs a signed quote or order form.
      </Banner>
      <nav aria-label="Configuration steps">
        <ol className="flex flex-wrap gap-2">
          {STEPS.map((s, i) => (
            <li key={s.id}>
              <Button
                size="sm"
                variant={s.id === p.step ? "primary" : "secondary"}
                aria-current={s.id === p.step ? "step" : undefined}
                isDisabled={i > index}
                onPress={() => p.onStep(s.id)}
              >
                {`${i + 1}. ${s.label}`}
              </Button>
            </li>
          ))}
        </ol>
      </nav>

      {p.step === "purpose" ? <PurposeStep {...p} /> : null}
      {p.step === "services" ? <ServicesStep {...p} /> : null}
      {p.step === "coverage" ? <CoverageStep {...p} /> : null}
      {p.step === "review" ? <ReviewStep {...p} /> : null}

      <div className="flex flex-wrap items-center gap-3">
        {index > 0 ? (
          <Button variant="secondary" onPress={() => p.onStep(STEPS[index - 1]!.id)}>
            Back
          </Button>
        ) : null}
        {p.step !== "review" ? (
          <Button isDisabled={!gate.ok} onPress={() => p.onStep(STEPS[index + 1]!.id)}>
            Continue
          </Button>
        ) : null}
        {!gate.ok && gate.why ? (
          <p role="status" className="text-sm text-ink-muted">
            {gate.why}
          </p>
        ) : null}
      </div>
    </div>
  );
}

function PurposeStep({ draft, onDraft }: BuilderProps) {
  return (
    <Card title="What are you setting this up for?" description="The purpose is recorded with your configuration. It does not change any policy or legal basis by itself.">
      <RadioCards
        label="Purpose"
        options={PURPOSES.map((x) => ({ id: x.id, label: x.label, description: x.description }))}
        value={draft.purpose}
        onChange={(v) => onDraft({ ...draft, purpose: v as Draft["purpose"] })}
        columns={2}
      />
    </Card>
  );
}

function ServicesStep({ catalogue, deps, draft, onDraft }: BuilderProps) {
  const missing = missingDependencies(draft.families, deps);
  const name = (code: string) => catalogue.find((c) => c.family === code)?.name ?? code;
  return (
    <Card title="Which services do you need?" description="Some services depend on others. A selection that cannot work is not allowed to continue.">
      {missing.length ? (
        <Banner tone="warn" title="Add what your selection depends on" live="polite">
          <ul className="list-disc pl-5">
            {missing.map((m) => (
              <li key={`${m.family}-${m.needs}`}>
                {name(m.family)} needs {name(m.needs)}.
              </li>
            ))}
          </ul>
        </Banner>
      ) : null}
      <fieldset className="mt-3 grid gap-2 sm:grid-cols-2">
        <legend className="sr-only">Services</legend>
        {catalogue.map((c) => {
          const blocks = dependents(draft.families, c.family, deps);
          return (
            <Checkbox
              key={c.family}
              isSelected={draft.families.includes(c.family)}
              onChange={() => onDraft({ ...draft, families: toggleFamily(draft.families, c.family) })}
              description={`Unit: ${c.unit}.${c.depends_on.length ? ` Needs ${c.depends_on.map(name).join(", ")}.` : ""}${blocks.length ? ` Required by ${blocks.map(name).join(", ")}.` : ""}`}
            >
              <span className="inline-flex flex-wrap items-center gap-2">
                {c.name}
                {c.regulated ? (
                  <Badge tone="warn" size="xs">
                    Regulated
                  </Badge>
                ) : null}
              </span>
            </Checkbox>
          );
        })}
      </fieldset>
    </Card>
  );
}

function CoverageStep({ draft, onDraft, preview, previewPending, previewError }: BuilderProps) {
  const errors = marketErrors(draft);
  return (
    <div className="space-y-4">
      <Card title="Where will you use it?" description="Each place is judged on its own: the country of the person being checked, the country of whoever pays, and the region your data is processed in.">
        <div className="grid gap-3 sm:grid-cols-3">
          <TextField label="Country of the person checked" description="Two letters, for example DE." value={draft.subjectCountry} onChange={(v) => onDraft({ ...draft, subjectCountry: v })} errorMessage={errors.subjectCountry} />
          <TextField label="Country of the paying company" description="Two letters, for example DE." value={draft.payerCountry} onChange={(v) => onDraft({ ...draft, payerCountry: v })} errorMessage={errors.payerCountry} />
          <TextField label="Data region" description="For example eu-central." value={draft.region} onChange={(v) => onDraft({ ...draft, region: v })} errorMessage={errors.region} />
        </div>
      </Card>
      <CoverageResult preview={preview} pending={previewPending} error={previewError} />
    </div>
  );
}

function CoverageResult({ preview, pending, error }: { preview: QuotePreview | undefined; pending: boolean; error: string | null }) {
  if (error) return <Banner tone="danger" title="Coverage could not be checked" live="assertive">{error}</Banner>;
  if (pending && !preview) return <p role="status" className="text-sm text-ink-muted">Checking coverage…</p>;
  if (!preview) return <p className="text-sm text-ink-muted">Name a country above to see coverage.</p>;
  return (
    <div className="space-y-3" aria-live="polite">
      {coverageGroups(preview.families).map((g) => (
        <Card key={g.id} title={g.title} description={g.help}>
          <ul className="space-y-2">
            {g.cards.map((c) => (
              <li key={c.family} className="text-sm">
                <span className="inline-flex flex-wrap items-center gap-2">
                  <span className="font-medium">{c.name}</span>
                  <Badge tone={stateTone(c.state)} size="xs">
                    {STATE_LABEL[c.state]}
                  </Badge>
                </span>
                {c.reasons.length ? (
                  <ul className="mt-1 list-disc pl-5 text-xs text-ink-muted">
                    {c.reasons.map((r) => (
                      <li key={r.cause}>{r.text}</li>
                    ))}
                  </ul>
                ) : null}
                {c.provider_attribution ? <p className="mt-1 text-xs text-ink-muted">{c.provider_attribution}</p> : null}
              </li>
            ))}
          </ul>
        </Card>
      ))}
    </div>
  );
}

function ReviewStep(p: BuilderProps) {
  const { preview, saved } = p;
  return (
    <div className="space-y-4">
      <Card title="Review" description="This is exactly what would go to the deal desk.">
        {preview ? (
          <div className="space-y-3 text-sm">
            <p>
              Reference <span className="font-mono text-xs">{preview.scope_hash.slice(0, 12)}</span>. The same choices always give the same reference; any change gives a new one.
            </p>
            {preview.problems.length ? (
              <Banner tone="danger" title="This selection cannot work yet" live="polite">
                {preview.problems.map((x) => `${x.family} needs ${x.needs}`).join("; ")}.
              </Banner>
            ) : null}
            <CoverageResult preview={preview} pending={p.previewPending} error={p.previewError} />
            <ul className="list-disc pl-5 text-xs text-ink-muted">
              {preview.assumptions.map((a) => (
                <li key={a}>{a}</li>
              ))}
            </ul>
            <p className="font-medium">{nextStep(preview.families)}</p>
          </div>
        ) : (
          <p className="text-sm text-ink-muted">{p.previewError ?? "Checking your configuration…"}</p>
        )}
      </Card>
      <div className="flex flex-wrap items-center gap-3">
        <Button isDisabled={!p.mayRequest || !preview || Boolean(preview?.problems.length)} pending={p.saving} onPress={p.onSave}>
          Save draft
        </Button>
        <Button
          variant="secondary"
          isDisabled={!p.mayRequest || !saved || saved.state !== "DRAFT"}
          pending={p.requesting}
          onPress={p.onRequest}
        >
          Send to the deal desk
        </Button>
        {!p.mayRequest ? <p className="text-sm text-ink-muted">Only company administrators can save or send a configuration.</p> : null}
      </div>
      {saved ? (
        <Banner tone={saved.state === "QUOTE_REQUESTED" ? "ok" : "info"} title={saved.state === "QUOTE_REQUESTED" ? "Sent to the deal desk" : "Draft saved"} live="polite">
          {saved.state === "QUOTE_REQUESTED"
            ? "They will come back with a quote you can review and approve. Nothing is bought or switched on."
            : expiryNote(saved.expires_at)}
        </Banner>
      ) : null}
    </div>
  );
}
