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
  MARKET_NOT_RELEASED: {
    title: "Identity verification hasn't launched in this country yet",
    body: "The checks needed to launch verification here are not complete, so we can't start one. Nothing was started or stored. Try again later, or contact support if you were told it should be available.",
  },
  CROSS_BORDER_ROUTE_DENIED: {
    title: "We can't verify you here with this service",
    body: "The service available for this country would handle your data outside the places the law allows, so we didn't use it. Nothing was started or stored. Contact support and quote the reference below.",
  },
  BIOMETRIC_ROUTE_NOT_PERMITTED: {
    title: "A face check isn't available for you here",
    body: "We can't use a face or other biometric check for you in this country. Nothing was started or stored. Your organization can offer another way to verify you, without a biometric check.",
  },
  PRIVACY_OVERLAY_UNAVAILABLE: {
    title: "Identity verification isn't ready for this country yet",
    body: "The data protection checks for this country are not approved yet, so we can't start a verification. Nothing was started or stored. Try again later.",
  },
  PROVIDER_SCOPE_SUSPENDED: {
    title: "Identity verification is temporarily stopped for this country",
    body: "We have paused the service used for verification here while we check it. Nothing was started or stored. Try again later, or contact support and quote the reference below.",
  },
  PROVIDER_NOT_ENTITLED: {
    title: "Identity verification is paused for this country",
    body: "The service we use for verification here isn't available to us right now, so we can't start one. Nothing was started or stored. Try again later, or contact support and quote the reference below.",
  },
};

export function regulatoryNotice(code: string | undefined): { title: string; body: string } | null {
  return code ? (REGULATORY_COPY[code] ?? null) : null;
}
