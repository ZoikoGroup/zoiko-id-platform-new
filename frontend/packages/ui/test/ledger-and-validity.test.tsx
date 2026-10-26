// @vitest-environment jsdom
/** UI acceptance checks for numbers and states that must not be misread (ZID-UX-001 §12, §13).
 *
 * UX-08 the available budget shown is limit - committed - reserved, exactly as the ledger defines it.
 * UX-09 cryptographic validity is labelled as validity, never as authority.
 * Server-side enforcement is proven by the backend tests named in the traceability rows. */
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { BudgetMeter, StatusBadge } from "../src";

const text = (n: React.ReactElement) => renderToStaticMarkup(n).replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();

describe("UX-08 mandate budget shows the ledger's available amount", () => {
  it("available is limit minus committed minus reserved", () => {
    const t = text(<BudgetMeter label="Calls" limit={1000} committed={300} reserved={150} />);
    expect(t).toContain("Limit 1,000");
    expect(t).toContain("Committed 300");
    expect(t).toContain("Reserved 150");
    expect(t).toContain("Available 550");
  });

  it("reserved funds are not available even though they are not yet committed", () => {
    expect(text(<BudgetMeter label="Calls" limit={100} committed={0} reserved={100} />)).toContain("Available 0");
  });

  it("an overdrawn ledger is shown as it is, never clamped up to zero", () => {
    expect(text(<BudgetMeter label="Calls" limit={100} committed={90} reserved={30} />)).toContain("Available -20");
  });

  it("value budgets keep the currency", () => {
    const t = text(<BudgetMeter label="Spend" limit={100000} committed={25000} reserved={0} currency="USD" />);
    expect(t).toContain("USD");
    expect(t).toContain("750.00");
  });
});

describe("UX-09 a valid credential is not presented as authorised", () => {
  it("the credential badge names what it is about", () => {
    const html = renderToStaticMarkup(<StatusBadge state="VALID" kind="Credential cryptographic status" />);
    expect(html).toContain("Credential cryptographic status: Valid");
  });

  it("validity and authority badges carry different names for the same word", () => {
    const validity = renderToStaticMarkup(<StatusBadge state="ACTIVE" kind="Credential cryptographic status" />);
    const authority = renderToStaticMarkup(<StatusBadge state="ACTIVE" kind="Grant authority state" />);
    expect(validity).not.toEqual(authority);
    expect(validity).toContain("Credential cryptographic status");
    expect(authority).toContain("Grant authority state");
  });
});
