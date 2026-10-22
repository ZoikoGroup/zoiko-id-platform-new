"use client";

import { Banner, Button, Checkbox } from "@zoiko/ui";
import { AlertTriangle, Check, Copy } from "lucide-react";
import { useState, type ReactNode } from "react";

/** A value the cell returns exactly once (only its hash is stored). Held in component state only. */
export function OneTimeValue({
  label,
  value,
  children,
  acknowledgement,
  onDone,
}: {
  label: string;
  value: string;
  children?: ReactNode;
  acknowledgement: string;
  onDone: () => void;
}) {
  const [copied, setCopied] = useState(false);
  const [ack, setAck] = useState(false);
  return (
    <div className="space-y-4">
      <Banner tone="warn" icon={AlertTriangle} title="Shown once" live="polite">
        This value cannot be displayed again. {children}
      </Banner>
      <div>
        <div className="mb-1.5 text-sm font-medium text-ink">{label}</div>
        <div className="flex items-stretch gap-2">
          <code aria-label={label} className="min-w-0 flex-1 overflow-x-auto rounded-control border border-border-strong bg-canvas px-3 py-2 font-mono text-sm text-ink">
            {value}
          </code>
          <Button
            variant="secondary"
            onPress={() => void navigator.clipboard?.writeText(value).then(() => setCopied(true))}
            icon={copied ? <Check aria-hidden className="size-4 text-ok-fg" /> : <Copy aria-hidden className="size-4" />}
          >
            {copied ? "Copied" : "Copy"}
          </Button>
        </div>
      </div>
      <Checkbox isSelected={ack} onChange={setAck}>
        {acknowledgement}
      </Checkbox>
      <div className="flex justify-end">
        <Button isDisabled={!ack} onPress={onDone}>
          Done
        </Button>
      </div>
    </div>
  );
}
