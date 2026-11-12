/** What a person is told when a country's policy pack stops a verification (ZID-RCG-003). The server's message names the capability and the
 * country in the platform's own words; this is the same refusal in the person's. Nothing has been started or stored in either case, and the
 * copy says so. An unknown code returns null, so the uniform error view handles it. */
const REGULATORY_COPY: Record<string, { title: string; body: string }> = {
  REGULATORY_POLICY_UNAVAILABLE: {
    title: "Identity verification isn't open for this country yet",
    body: "We can't start a verification here until the rules for this country have been approved. Nothing was started or stored. Try again later, or contact support if you were told it should be available.",
  },
  REGULATORY_REVIEW_REQUIRED: {
    title: "This country needs a manual review first",
    body: "We need to confirm the rules for your region before a verification can start. Nothing was started or stored. Contact support and quote the reference below.",
  },
};

export function regulatoryNotice(code: string | undefined): { title: string; body: string } | null {
  return code ? (REGULATORY_COPY[code] ?? null) : null;
}
