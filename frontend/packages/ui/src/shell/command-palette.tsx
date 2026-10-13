"use client";

/** Command palette (§3.1): navigation and safe read actions only. Destructive operations are never
 * commands here. Search results come from the backend's authorization-aware, tenant-scoped search
 * (§25, UX-01); the palette never fabricates or caches cross-tenant suggestions. */
import { CornerDownLeft, Search, type LucideIcon } from "lucide-react";
import { useEffect, useId, useMemo, useRef, useState } from "react";
import { Dialog as AriaDialog, Modal, ModalOverlay } from "react-aria-components";

import { cn } from "../utils/cn";

export type Command = {
  id: string;
  label: string;
  group: string;
  href: string;
  icon?: LucideIcon;
  hint?: string;
  keywords?: string[];
};

export type SearchProvider = (query: string, signal: AbortSignal) => Promise<Command[]>;

export function CommandPalette({
  isOpen,
  onOpenChange,
  commands,
  search,
  onNavigate,
}: {
  isOpen: boolean;
  onOpenChange: (open: boolean) => void;
  commands: Command[];
  search?: SearchProvider;
  onNavigate: (href: string) => void;
}) {
  const [query, setQuery] = useState("");
  const [remote, setRemote] = useState<Command[]>([]);
  const [active, setActive] = useState(0);
  const listId = useId();
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!isOpen) {
      setQuery("");
      setRemote([]);
      setActive(0);
    }
  }, [isOpen]);

  useEffect(() => {
    if (!search || query.trim().length < 2) {
      setRemote([]);
      return;
    }
    const ctrl = new AbortController();
    const t = setTimeout(() => {
      search(query.trim(), ctrl.signal)
        .then(setRemote)
        .catch(() => setRemote([]));
    }, 180);
    return () => {
      clearTimeout(t);
      ctrl.abort();
    };
  }, [query, search]);

  const results = useMemo(() => {
    const q = query.trim().toLowerCase();
    const local = q
      ? commands.filter((c) => [c.label, c.group, ...(c.keywords ?? [])].some((s) => s.toLowerCase().includes(q)))
      : commands;
    return [...remote, ...local].slice(0, 40);
  }, [commands, remote, query]);

  const groups = useMemo(() => {
    const m = new Map<string, Command[]>();
    for (const r of results) m.set(r.group, [...(m.get(r.group) ?? []), r]);
    return [...m.entries()];
  }, [results]);

  function go(c: Command | undefined) {
    if (!c) return;
    onOpenChange(false);
    onNavigate(c.href);
  }

  let index = -1;
  return (
    <ModalOverlay isOpen={isOpen} onOpenChange={onOpenChange} isDismissable className="fixed inset-0 z-50 bg-scrim entering:animate-fade-in">
      <Modal className="mx-auto mt-[12vh] w-[calc(100%-2rem)] max-w-xl overflow-hidden rounded-xl border border-border bg-raised shadow-pop">
        <AriaDialog aria-label="Command palette" className="outline-none">
          <div className="flex items-center gap-2 border-b border-border px-4">
            <Search aria-hidden className="size-4 text-ink-subtle" />
            <input
              ref={inputRef}
              autoFocus
              role="combobox"
              aria-expanded="true"
              aria-controls={listId}
              aria-activedescendant={results[active] ? `${listId}-${active}` : undefined}
              aria-autocomplete="list"
              aria-label="Search pages and objects"
              placeholder="Search pages and objects you can access…"
              value={query}
              onChange={(e) => {
                setQuery(e.target.value);
                setActive(0);
              }}
              onKeyDown={(e) => {
                if (e.key === "ArrowDown") {
                  e.preventDefault();
                  setActive((a) => Math.min(results.length - 1, a + 1));
                } else if (e.key === "ArrowUp") {
                  e.preventDefault();
                  setActive((a) => Math.max(0, a - 1));
                } else if (e.key === "Enter") {
                  e.preventDefault();
                  go(results[active]);
                }
              }}
              className="h-12 min-w-0 flex-1 bg-transparent text-sm text-ink outline-none placeholder:text-ink-subtle"
            />
            <kbd className="rounded border border-border px-1.5 py-0.5 font-mono text-2xs text-ink-subtle">Esc</kbd>
          </div>
          <div id={listId} role="listbox" aria-label="Results" className="max-h-[50vh] overflow-y-auto p-2">
            {results.length === 0 ? (
              <p className="px-3 py-6 text-center text-sm text-ink-muted">No matching pages or objects in your scope.</p>
            ) : (
              groups.map(([group, items]) => (
                <div key={group} role="group" aria-label={group} className="mb-1">
                  <div className="px-3 pb-1 pt-2 text-2xs font-semibold uppercase tracking-wider text-ink-subtle">{group}</div>
                  {items.map((c) => {
                    index += 1;
                    const i = index;
                    const Icon = c.icon;
                    return (
                      <div
                        key={c.id}
                        id={`${listId}-${i}`}
                        role="option"
                        aria-selected={i === active}
                        onMouseMove={() => setActive(i)}
                        onClick={() => go(c)}
                        className={cn(
                          "flex cursor-default items-center gap-3 rounded-md px-3 py-2 text-sm",
                          i === active ? "bg-brand-50 text-brand-700" : "text-ink",
                        )}
                      >
                        {Icon ? <Icon aria-hidden className="size-4 shrink-0 opacity-70" /> : null}
                        <span className="min-w-0 flex-1 truncate">{c.label}</span>
                        {c.hint ? <span className="truncate text-xs text-ink-subtle">{c.hint}</span> : null}
                        {i === active ? <CornerDownLeft aria-hidden className="size-3.5 opacity-60" /> : null}
                      </div>
                    );
                  })}
                </div>
              ))
            )}
          </div>
          <div className="border-t border-border px-4 py-2 text-2xs text-ink-subtle">
            Navigation and read-only actions only. Changes are made from the object page.
          </div>
        </AriaDialog>
      </Modal>
    </ModalOverlay>
  );
}
