/** UI acceptance checks that need no browser (ZID-UX-001 §33): server-rendered markup is enough to
 * prove status, risk and table semantics. Interactive keyboard flows (UX-18, UX-21) are covered by the
 * browser suite in frontend/tests/e2e. */
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { Badge, DataTable, RiskBadge, StatusBadge, type Column } from "../src";

const html = (node: React.ReactElement) => renderToStaticMarkup(node);

describe("UX-20 colour independence: state is text + icon + accessible label, never colour alone", () => {
  it.each(["ACTIVE", "REVOKED", "PAUSED", "EXHAUSTED", "PENDING_VERIFICATION", "DELIVERED"])("%s", (state) => {
    const out = html(<StatusBadge state={state} kind="Endpoint status" />);
    expect(out).toContain("<svg"); // an icon
    expect(out).toMatch(/class="sr-only">Endpoint status: /); // read to assistive tech with what it is about
    expect(out).toContain('aria-hidden="true">'); // the icon and the short visible text are not read twice
    expect(out.replace(/<[^>]+>/g, "").trim().length).toBeGreaterThan(2); // visible text
  });

  it("an unknown state is neutral text, not an empty or green badge", () => {
    const out = html(<StatusBadge state="SOMETHING_NEW" kind="Thing" />);
    expect(out).toContain("Something new");
    expect(out).not.toContain("ok-bg");
  });

  it("a missing state renders as unknown, never as healthy", () => {
    const out = html(<StatusBadge state={null} />);
    expect(out.toLowerCase()).toContain("unknown");
    expect(out).not.toContain("ok-bg");
  });

  it("risk is words as well as a class", () => {
    expect(html(<RiskBadge risk="R3" />)).toContain("Critical action");
    expect(html(<RiskBadge risk={2} />)).toContain("High");
  });

  it("danger states carry the danger tone while healthy states do not", () => {
    expect(html(<StatusBadge state="REVOKED" />)).toContain("danger-bg");
    expect(html(<StatusBadge state="ACTIVE" />)).toContain("ok-bg");
  });
});

describe("UX-19 screen reader: tables have a name and column headers", () => {
  type Row = { id: string; name: string };
  const rows: Row[] = [{ id: "1", name: "Payroll" }];
  const columns: Column<Row>[] = [
    { key: "name", header: "Name", render: (r) => r.name },
    { key: "id", header: "ID", render: (r) => r.id },
  ];

  it("names the table and scopes its headers", () => {
    const out = html(<DataTable caption="Applications" columns={columns} rows={rows} rowKey={(r) => r.id} />);
    expect(out).toContain("Applications");
    expect(out).toMatch(/<th[^>]*scope="col"|<th[^>]*role="columnheader"/);
    expect(out).toContain("Payroll");
  });

  it("an empty table explains itself and offers the next step instead of showing nothing", () => {
    const out = html(
      <DataTable caption="Applications" columns={columns} rows={[]} rowKey={(r) => r.id} empty={<p>No applications yet</p>} />,
    );
    expect(out).toContain("No applications yet");
  });
});

describe("badge text is escaped (no HTML from tenant or provider content, UX §29)", () => {
  it("does not render markup passed as text", () => {
    const out = html(<Badge>{"<img src=x onerror=alert(1)>"}</Badge>);
    expect(out).not.toContain("<img");
    expect(out).toContain("&lt;img");
  });
});
