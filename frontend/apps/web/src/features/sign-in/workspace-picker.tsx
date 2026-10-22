"use client";

import { Button, TextField } from "@zoiko/ui";
import { Building2 } from "lucide-react";
import { useState, type FormEvent } from "react";

/** Which company you are signing in to. Switching reloads the page so the sign-in uses that
 * company's console client; it chooses where you authenticate, it grants nothing. */
export function WorkspacePicker({ current }: { current: string | null }) {
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function choose(e: FormEvent) {
    e.preventDefault();
    setPending(true);
    setError(null);
    const res = await fetch("/api/workspace", {
      method: "POST",
      headers: { "content-type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({ workspace: name.trim().toLowerCase() }),
    }).catch(() => null);
    if (res?.ok) {
      window.location.reload();
      return;
    }
    const body = (await res?.json().catch(() => ({}))) as { error?: { message?: string } } | undefined;
    setError(body?.error?.message ?? "That workspace could not be found.");
    setPending(false);
  }

  async function reset() {
    await fetch("/api/workspace", { method: "DELETE", credentials: "same-origin" }).catch(() => null);
    window.location.reload();
  }

  if (!editing) {
    return (
      <div className="flex items-center justify-between gap-3 rounded-card border border-border bg-surface px-4 py-3 text-sm">
        <span className="flex min-w-0 items-center gap-2 text-ink">
          <Building2 aria-hidden className="size-4 shrink-0 text-ink-subtle" />
          <span className="truncate">
            {current ? (
              <>
                Signing in to <strong>{current}</strong>
              </>
            ) : (
              "Signing in to the default workspace"
            )}
          </span>
        </span>
        <span className="flex shrink-0 gap-1">
          {current ? (
            <Button size="xs" variant="ghost" onPress={() => void reset()}>
              Default
            </Button>
          ) : null}
          <Button size="xs" variant="secondary" onPress={() => setEditing(true)}>
            Change
          </Button>
        </span>
      </div>
    );
  }
  return (
    <form className="space-y-3 rounded-card border border-border bg-surface p-4" onSubmit={(e) => void choose(e)}>
      <TextField
        label="Company workspace name"
        isRequired
        mono
        value={name}
        onChange={setName}
        errorMessage={error ?? undefined}
        description="The name your administrator gave the workspace, e.g. acme."
      />
      <div className="flex justify-end gap-2">
        <Button variant="secondary" size="sm" onPress={() => setEditing(false)}>
          Cancel
        </Button>
        <Button type="submit" size="sm" pending={pending} isDisabled={!name.trim()}>
          Continue
        </Button>
      </div>
    </form>
  );
}
