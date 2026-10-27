"use client";

import { Checkbox, TextField } from "@zoiko/ui";

import { listInput } from "@/lib/cell";

export type Choice = { id: string; label: string };

/** What a company can most often hand out. Each is a name the platform already knows, so nothing is typed from memory. */
export const ACTION_CHOICES: Choice[] = [
  { id: "payment.release", label: "Release a payment" },
  { id: "mandate.create", label: "Give someone a spending limit (mandate)" },
  { id: "purchase.create", label: "Create a purchase" },
  { id: "invoice.approve", label: "Approve an invoice" },
];

export const RESOURCE_CHOICES: Choice[] = [
  { id: "payment:*", label: "Any payment" },
  { id: "mandate:*", label: "Any spending limit (mandate)" },
  { id: "purchase:*", label: "Any purchase" },
  { id: "invoice:*", label: "Any invoice" },
];

/**
 * Pick from a list instead of typing exact names. The value stays the text the form already sends (one entry per line),
 * so the ticked boxes and the "something else" box are two views of the same list; entries that are not in the list are
 * kept in the box so nothing is lost.
 */
export function ChoiceList({
  label,
  description,
  choices,
  value,
  onChange,
  otherLabel,
  otherPlaceholder,
}: {
  label: string;
  description: string;
  choices: Choice[];
  value: string;
  onChange: (v: string) => void;
  otherLabel: string;
  otherPlaceholder: string;
}) {
  const chosen = listInput(value);
  const known = new Set(choices.map((c) => c.id));
  const custom = chosen.filter((v) => !known.has(v));

  const toggle = (id: string, on: boolean) =>
    onChange([...choices.map((c) => c.id).filter((i) => (i === id ? on : chosen.includes(i))), ...custom].join("\n"));

  return (
    <fieldset className="space-y-2">
      <legend className="text-sm font-medium text-ink">
        {label} <span aria-hidden className="text-danger-fg">*</span>
      </legend>
      <p className="text-xs text-ink-subtle">{description}</p>
      <div className="space-y-2 rounded-card border border-border p-3">
        {choices.map((c) => (
          <Checkbox key={c.id} isSelected={chosen.includes(c.id)} onChange={(on) => toggle(c.id, on)}>
            {c.label} <code className="ms-1 rounded bg-sunken px-1 font-mono text-xs text-ink-muted">{c.id}</code>
          </Checkbox>
        ))}
        <details open={custom.length > 0}>
          <summary className="cursor-pointer pt-1 text-xs font-medium text-brand-700">Something else</summary>
          <div className="pt-2">
            <TextField
              label={otherLabel}
              value={custom.join(", ")}
              onChange={(t) => onChange([...choices.map((c) => c.id).filter((i) => chosen.includes(i)), ...listInput(t)].join("\n"))}
              mono
              placeholder={otherPlaceholder}
            />
          </div>
        </details>
      </div>
    </fieldset>
  );
}
