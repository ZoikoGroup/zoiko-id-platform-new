"use client";

import { Check, ChevronDown, Minus } from "lucide-react";
import type { ReactNode } from "react";
import {
  Checkbox as AriaCheckbox,
  FieldError,
  Input,
  Label,
  ListBox,
  ListBoxItem,
  Popover,
  Radio,
  RadioGroup as AriaRadioGroup,
  Select as AriaSelect,
  SelectValue,
  Switch as AriaSwitch,
  Text,
  TextArea,
  TextField as AriaTextField,
  Button as AriaButton,
  type Key,
} from "react-aria-components";

import { cn } from "../utils/cn";

export const inputClass =
  "w-full rounded-control border border-border-strong bg-surface px-3 py-2 text-sm text-ink placeholder:text-ink-subtle shadow-xs " +
  "outline-none focus:border-brand-500 focus:ring-2 focus:ring-brand-500/25 data-invalid:border-danger-fg disabled:bg-sunken disabled:text-ink-subtle";

const labelClass = "text-sm font-medium text-ink";

function RequiredMark() {
  return (
    <>
      <span aria-hidden className="text-danger-fg"> *</span>
    </>
  );
}

export type TextFieldProps = {
  label: string;
  value?: string;
  defaultValue?: string;
  onChange?: (v: string) => void;
  description?: ReactNode;
  errorMessage?: string;
  placeholder?: string;
  type?: "text" | "email" | "url" | "password" | "number" | "datetime-local" | "date" | "search";
  isRequired?: boolean;
  isDisabled?: boolean;
  isReadOnly?: boolean;
  multiline?: boolean;
  rows?: number;
  mono?: boolean;
  name?: string;
  autoComplete?: string;
  className?: string;
  inputMode?: "text" | "numeric" | "decimal" | "email" | "url";
};

export function TextField({
  label,
  description,
  errorMessage,
  multiline,
  rows = 4,
  mono,
  placeholder,
  type = "text",
  className,
  autoComplete,
  inputMode,
  ...props
}: TextFieldProps) {
  return (
    <AriaTextField {...props} type={type} isInvalid={Boolean(errorMessage)} className={cn("flex flex-col gap-1.5", className)}>
      <Label className={labelClass}>
        {label}
        {props.isRequired ? <RequiredMark /> : null}
      </Label>
      {multiline ? (
        <TextArea rows={rows} placeholder={placeholder} className={cn(inputClass, mono && "font-mono text-xs")} />
      ) : (
        <Input
          placeholder={placeholder}
          autoComplete={autoComplete}
          inputMode={inputMode}
          className={cn(inputClass, mono && "font-mono")}
        />
      )}
      {description ? (
        <Text slot="description" className="text-xs text-ink-subtle">
          {description}
        </Text>
      ) : null}
      <FieldError className="text-xs font-medium text-danger-fg">{errorMessage}</FieldError>
    </AriaTextField>
  );
}

export type Option = { id: string; label: string; description?: string };

export function Select({
  label,
  options,
  value,
  onChange,
  description,
  errorMessage,
  placeholder = "Select…",
  isRequired,
  isDisabled,
  className,
  hideLabel = false,
}: {
  label: string;
  options: Option[];
  value?: string | null;
  onChange?: (v: string) => void;
  description?: ReactNode;
  errorMessage?: string;
  placeholder?: string;
  isRequired?: boolean;
  isDisabled?: boolean;
  className?: string;
  hideLabel?: boolean;
}) {
  return (
    <AriaSelect
      selectedKey={value ?? null}
      onSelectionChange={(k: Key | null) => k !== null && onChange?.(String(k))}
      isRequired={isRequired}
      isDisabled={isDisabled}
      isInvalid={Boolean(errorMessage)}
      placeholder={placeholder}
      className={cn("flex flex-col gap-1.5", className)}
    >
      <Label className={cn(labelClass, hideLabel && "sr-only")}>
        {label}
        {isRequired ? <RequiredMark /> : null}
      </Label>
      <AriaButton className={cn(inputClass, "flex items-center justify-between gap-2 text-start")}>
        <SelectValue className="truncate data-placeholder:text-ink-subtle" />
        <ChevronDown aria-hidden className="size-4 shrink-0 text-ink-subtle" />
      </AriaButton>
      {description ? (
        <Text slot="description" className="text-xs text-ink-subtle">
          {description}
        </Text>
      ) : null}
      <FieldError className="text-xs font-medium text-danger-fg">{errorMessage}</FieldError>
      <Popover className="min-w-(--trigger-width) rounded-lg border border-border bg-raised p-1 shadow-pop entering:animate-fade-in">
        <ListBox className="max-h-72 overflow-auto outline-none">
          {options.map((o) => (
            <ListBoxItem
              key={o.id}
              id={o.id}
              textValue={o.label}
              className="group flex cursor-default items-start gap-2 rounded-md px-3 py-2 text-sm text-ink outline-none focus:bg-hover selected:font-medium"
            >
              <span className="min-w-0 flex-1">
                <span className="block">{o.label}</span>
                {o.description ? <span className="block text-xs text-ink-subtle">{o.description}</span> : null}
              </span>
              <Check aria-hidden className="invisible size-4 shrink-0 text-brand-600 group-selected:visible" />
            </ListBoxItem>
          ))}
        </ListBox>
      </Popover>
    </AriaSelect>
  );
}

export function Checkbox({
  children,
  isSelected,
  isIndeterminate,
  isDisabled,
  onChange,
  "aria-label": ariaLabel,
  description,
}: {
  children?: ReactNode;
  isSelected?: boolean;
  isIndeterminate?: boolean;
  isDisabled?: boolean;
  onChange?: (v: boolean) => void;
  "aria-label"?: string;
  description?: ReactNode;
}) {
  return (
    <AriaCheckbox
      isSelected={isSelected}
      isIndeterminate={isIndeterminate}
      isDisabled={isDisabled}
      onChange={onChange}
      aria-label={ariaLabel}
      className="group flex items-start gap-2 text-sm text-ink data-disabled:opacity-50"
    >
      <span
        aria-hidden
        className="mt-0.5 flex size-4 shrink-0 items-center justify-center rounded border border-border-strong bg-surface text-white group-selected:border-brand-600 group-selected:bg-brand-600 group-indeterminate:border-brand-600 group-indeterminate:bg-brand-600 group-focus-visible:ring-2 group-focus-visible:ring-brand-500 group-focus-visible:ring-offset-1"
      >
        <Check className="hidden size-3 stroke-3 group-selected:block" />
        <Minus className="hidden size-3 stroke-3 group-indeterminate:block" />
      </span>
      {children || description ? (
        <span className="min-w-0">
          {children}
          {description ? <span className="block text-xs text-ink-subtle">{description}</span> : null}
        </span>
      ) : null}
    </AriaCheckbox>
  );
}

export function Switch({
  children,
  isSelected,
  onChange,
  isDisabled,
}: {
  children: ReactNode;
  isSelected?: boolean;
  onChange?: (v: boolean) => void;
  isDisabled?: boolean;
}) {
  return (
    <AriaSwitch
      isSelected={isSelected}
      onChange={onChange}
      isDisabled={isDisabled}
      className="group flex items-center gap-2.5 text-sm text-ink data-disabled:opacity-50"
    >
      <span
        aria-hidden
        className="flex h-5 w-9 shrink-0 items-center rounded-full bg-border-strong p-0.5 transition-colors group-selected:bg-brand-600 group-focus-visible:ring-2 group-focus-visible:ring-brand-500 group-focus-visible:ring-offset-2"
      >
        <span className="size-4 rounded-full bg-white shadow transition-transform group-selected:translate-x-4 rtl:group-selected:-translate-x-4" />
      </span>
      {children}
    </AriaSwitch>
  );
}

/** Radio cards: each option explains its consequence, used for continuity modes, scopes etc. */
export function RadioCards({
  label,
  options,
  value,
  onChange,
  description,
  columns = 1,
}: {
  label: string;
  options: (Option & { disabled?: boolean; badge?: ReactNode })[];
  value?: string | null;
  onChange?: (v: string) => void;
  description?: ReactNode;
  columns?: 1 | 2 | 3;
}) {
  return (
    <AriaRadioGroup value={value ?? null} onChange={onChange} className="flex flex-col gap-2">
      <Label className={labelClass}>{label}</Label>
      {description ? (
        <Text slot="description" className="-mt-1 text-xs text-ink-subtle">
          {description}
        </Text>
      ) : null}
      <div className={cn("grid gap-2", columns === 2 && "sm:grid-cols-2", columns === 3 && "sm:grid-cols-3")}>
        {options.map((o) => (
          <Radio
            key={o.id}
            value={o.id}
            isDisabled={o.disabled}
            className="group flex cursor-default gap-3 rounded-lg border border-border bg-surface p-3 outline-none transition-colors hover:border-border-strong selected:border-brand-600 selected:bg-brand-50/60 focus-visible:ring-2 focus-visible:ring-brand-500 data-disabled:opacity-50"
          >
            <span
              aria-hidden
              className="mt-0.5 flex size-4 shrink-0 items-center justify-center rounded-full border border-border-strong group-selected:border-brand-600"
            >
              <span className="hidden size-2 rounded-full bg-brand-600 group-selected:block" />
            </span>
            <span className="min-w-0 flex-1">
              <span className="flex flex-wrap items-center gap-2 text-sm font-medium text-ink">
                {o.label}
                {o.badge}
              </span>
              {o.description ? <span className="mt-0.5 block text-xs text-ink-muted">{o.description}</span> : null}
            </span>
          </Radio>
        ))}
      </div>
    </AriaRadioGroup>
  );
}

/** Canonical reason code (required) plus an optional note. Free text alone never drives an
 * evidence-bearing outcome (ZID-UX-001 §8.1, §36). */
export function ReasonCodeField({
  reasons,
  code,
  onCodeChange,
  note,
  onNoteChange,
  errorMessage,
}: {
  reasons: Option[];
  code: string | null;
  onCodeChange: (v: string) => void;
  note: string;
  onNoteChange: (v: string) => void;
  errorMessage?: string;
}) {
  return (
    <div className="space-y-3">
      <Select
        label="Reason code"
        isRequired
        options={reasons}
        value={code}
        onChange={onCodeChange}
        errorMessage={errorMessage}
        description="Recorded in the evidence receipt."
      />
      <TextField label="Note (optional)" multiline rows={2} value={note} onChange={onNoteChange} description="Supplements the reason code; it cannot replace it." />
    </div>
  );
}

export function FieldGroup({ legend, description, children }: { legend: string; description?: ReactNode; children: ReactNode }) {
  return (
    <fieldset className="space-y-4">
      <legend className="text-sm font-semibold text-ink">{legend}</legend>
      {description ? <p className="-mt-2 text-sm text-ink-muted">{description}</p> : null}
      {children}
    </fieldset>
  );
}
