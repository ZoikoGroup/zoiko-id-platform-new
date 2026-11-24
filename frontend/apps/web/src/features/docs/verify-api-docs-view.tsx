import type { Block, Example, Section } from "./verify-api-docs";

const LANG_LABEL: Record<Example["lang"], string> = { curl: "curl", node: "Node.js", python: "Python" };

function Code({ children }: { children: string }) {
  return (
    <pre className="overflow-x-auto rounded-lg border border-border bg-surface-sunken p-3 text-xs leading-relaxed">
      <code>{children}</code>
    </pre>
  );
}

function BlockView({ b }: { b: Block }) {
  switch (b.kind) {
    case "p":
      return <p className="text-sm leading-relaxed text-ink">{b.text}</p>;
    case "list":
      return (
        <ul className="list-disc space-y-1.5 pl-5 text-sm leading-relaxed text-ink">
          {b.items.map((i) => (
            <li key={i}>{i}</li>
          ))}
        </ul>
      );
    case "note":
      return (
        <div role="note" className={`rounded-lg border p-3 text-sm ${b.tone === "warn" ? "border-warn-fg/25 bg-warn-bg" : "border-info-fg/20 bg-info-bg"}`}>
          <p className="font-semibold text-ink">{b.title}</p>
          <p className="text-ink-muted">{b.text}</p>
        </div>
      );
    case "json":
      return (
        <figure className="space-y-1">
          <figcaption className="text-xs font-medium text-ink-muted">{b.caption}</figcaption>
          <Code>{b.code}</Code>
        </figure>
      );
    case "code":
      return (
        <figure className="space-y-2">
          <figcaption className="text-xs font-medium text-ink-muted">{b.caption}</figcaption>
          {b.examples.map((e, i) => (
            <details key={e.lang} open={i === 0} className="rounded-lg border border-border">
              <summary className="cursor-pointer px-3 py-1.5 text-sm font-medium text-ink">{LANG_LABEL[e.lang]}</summary>
              <div className="p-2 pt-0">
                <Code>{e.code}</Code>
              </div>
            </details>
          ))}
        </figure>
      );
    case "table":
      return (
        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-left text-sm">
            <thead>
              <tr>
                {b.head.map((h) => (
                  <th key={h} scope="col" className="border-b border-border-strong px-2 py-1.5 text-xs font-semibold text-ink-muted">
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {b.rows.map((r) => (
                <tr key={r.join("|")} className="align-top">
                  {r.map((c, i) => (
                    <td key={i} className={`border-b border-border px-2 py-1.5 ${i === 0 ? "font-mono text-xs" : ""}`}>
                      {c}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      );
  }
}

/** The documentation sections, with a contents list that links to each. */
export function DocsView({ sections }: { sections: Section[] }) {
  return (
    <div className="space-y-10">
      <nav aria-label="Contents" className="rounded-lg border border-border p-3">
        <p className="mb-1 text-xs font-semibold text-ink-muted">On this page</p>
        <ul className="grid gap-x-6 gap-y-1 text-sm sm:grid-cols-2">
          {sections.map((s) => (
            <li key={s.id}>
              <a className="text-brand-600 underline-offset-2 hover:underline" href={`#${s.id}`}>
                {s.title}
              </a>
            </li>
          ))}
        </ul>
      </nav>
      {sections.map((s) => (
        <section key={s.id} id={s.id} aria-labelledby={`${s.id}-h`} className="scroll-mt-6 space-y-3">
          <h2 id={`${s.id}-h`} className="text-xl font-bold tracking-tight text-ink">
            {s.title}
          </h2>
          {s.blocks.map((b, i) => (
            <BlockView key={i} b={b} />
          ))}
        </section>
      ))}
    </div>
  );
}
