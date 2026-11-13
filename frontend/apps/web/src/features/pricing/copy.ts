/** The only marketing claims on the pricing page (ZID-COM-001 §19) and the meter glossary (§7). A test scans
 * every string here for certification, compliance and availability wording. */
export const HERO = "Verify identity. Validate credentials. Prove authority.";
export const SUBHERO =
  "One platform to verify identity, validate qualifications, prove authority, and exchange trusted evidence, under explicit market, privacy and commercial controls.";

export const PLAN_ORDER = ["DEVELOPER", "LAUNCH", "BUSINESS", "SCALE", "ENTERPRISE"] as const;

export const SKU_LABEL: Record<string, string> = {
  "ZID-BASE": "Platform fee (per month)",
  "ZID-HUM": "Active person above the included amount",
  "ZID-AUTH": "Sign-ins above the included amount",
  "ZID-PDP": "Access decisions above the included amount",
  "ZID-M2M": "Machine grants above the included amount",
  "ZID-FED": "Extra SSO connection (per month)",
  "ZID-SCIM": "Extra directory connection (per month)",
  "ZID-VCI": "Credential issued",
  "ZID-VCP": "Credential presentation checked",
};

export const GLOSSARY: { term: string; meaning: string }[] = [
  {
    term: "Monthly active person",
    meaning:
      "One distinct person with at least one completed production sign-in or qualifying action in a calendar month (UTC), counted once inside your organization even if their identities are linked or merged.",
  },
  {
    term: "Sign-in",
    meaning: "One successful sign-in. Redirects, extra factors, token refreshes, failed attempts and retries are not counted.",
  },
  {
    term: "Access decision",
    meaning: "One final answer from the policy engine, whether it allows or denies. Retries and cached answers are not counted.",
  },
  {
    term: "Machine grant",
    meaning: "One scoped credential issued to a service or agent in production.",
  },
  {
    term: "Credential issued / presentation checked",
    meaning: "One credential successfully issued, or one presentation cryptographically checked.",
  },
];

export const ADD_ONS =
  "Identity and business verification, screening, messaging, extended archives, dedicated regions and professional services are priced separately, depend on country and supplier, and are quoted rather than listed here.";

export const DISCLAIMER =
  "Prices are in the currency shown, exclude taxes and third-party checks, and may change for new orders. The calculator gives an estimate, not a quotation; your order form is the only binding price.";

export const ENTERPRISE_NOTE = "Enterprise terms, volumes and the platform fee are negotiated; the fee shown is a starting point.";

/** Plain answers to the questions a buyer asks about billing, testing and coverage (ZID-UX-PRICE-004 WF-P*, component P-09). Each states how the platform behaves and promises
 * nothing the contract has to settle. A test scans every string here for certification, compliance and availability wording. */
export const FAQ: { q: string; a: string }[] = [
  {
    q: "What counts as one charge?",
    a: "One charge applies to one contracted logical check. A retry of the same operation is not billed twice. How an abandoned session or a failed provider call is treated is set out with each service in your order form.",
  },
  {
    q: "Is there a trial?",
    a: "There is no automatic trial. The developer sandbox is for testing and carries no live charges. A live pilot needs separate approval, has a stated cap and end date, and never converts to a paid plan by itself.",
  },
  {
    q: "How do I know a service is offered where I operate?",
    a: "Choose your market above. Each service is judged for the country of the person being checked, the country of the paying company and the data region, and the answer says why when a service is not offered.",
  },
  {
    q: "Why do some services need a conversation with Sales?",
    a: "Services such as electronic signatures, regional or dedicated environments and manual review depend on rights, partners and feasibility that are agreed per market before they can be offered.",
  },
  {
    q: "What happens to my records if I stop a paid service?",
    a: "Ending a paid service does not remove lawful access to your records or the evidence the law requires to be kept. Retention and data rights follow your contract and the rules of your market.",
  },
  {
    q: "Do the estimates bind us to a price?",
    a: "No. An estimate is for planning. Only a signed quote or order form is a binding price, and it names the seller, the payer, the currency, the tax treatment and the version it was made under.",
  },
];

export const PLATFORM_NOTE =
  "These plans cover the platform itself: people, sign-ins, access decisions, machine grants and connections. Verification, credential and signature services are priced separately by service and market.";
