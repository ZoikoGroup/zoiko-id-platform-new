/** The documents a person may hold, grouped as the reference product groups them. The same list for every country: the country does not change what is offered.
 *
 * Only an identity document is verified here (a photograph and a live selfie, compared by the platform). An address or other document is evidence of something else and is never
 * treated as an identity document: a utility bill proves where someone lives, not who they are. */

export type DocumentCategory = "identity" | "address" | "other";

export const CATEGORY_LABEL: Record<DocumentCategory, string> = {
  identity: "Identity document",
  address: "Address or residency document",
  other: "Other document",
};

export const CATEGORY_HINT: Record<DocumentCategory, string> = {
  identity: "A government-issued document that proves who you are.",
  address: "A bill, statement or agreement that proves where you live.",
  other: "Anything else, such as a birth or marriage certificate.",
};

export type DocumentOption = { value: string; label: string };

export const DOCUMENTS_BY_CATEGORY: Record<DocumentCategory, DocumentOption[]> = {
  identity: [
    { value: "passport", label: "Passport" },
    { value: "driving_license", label: "Driving licence" },
    { value: "national_id", label: "National ID card" },
    { value: "aadhaar", label: "Aadhaar" },
    { value: "pan_card", label: "PAN card" },
    { value: "voter_id", label: "Voter ID" },
    { value: "residence_permit", label: "Residence permit" },
    { value: "permanent_resident_card", label: "Permanent resident card" },
    { value: "government_photo_id", label: "Government photo ID" },
    { value: "government_employee_id", label: "Government employee ID" },
  ],
  address: [
    { value: "electricity_bill", label: "Electricity bill" },
    { value: "water_bill", label: "Water bill" },
    { value: "gas_bill", label: "Gas bill" },
    { value: "telephone_bill", label: "Telephone bill" },
    { value: "internet_bill", label: "Internet bill" },
    { value: "property_tax_bill", label: "Property tax bill" },
    { value: "bank_statement", label: "Bank statement" },
    { value: "credit_card_statement", label: "Credit card statement" },
    { value: "government_address_certificate", label: "Government address certificate" },
    { value: "rental_agreement", label: "Rental agreement" },
  ],
  other: [
    { value: "birth_certificate", label: "Birth certificate" },
    { value: "marriage_certificate", label: "Marriage certificate" },
    { value: "other_government_document", label: "Other government document" },
    { value: "other", label: "Other" },
  ],
};

export const CATEGORIES = Object.keys(DOCUMENTS_BY_CATEGORY) as DocumentCategory[];

export const IDENTITY_DOCUMENTS = DOCUMENTS_BY_CATEGORY.identity;

export function documentLabel(value: string): string {
  for (const list of Object.values(DOCUMENTS_BY_CATEGORY)) {
    const hit = list.find((d) => d.value === value);
    if (hit) return hit.label;
  }
  return value;
}

export function categoryOf(value: string): DocumentCategory | null {
  return CATEGORIES.find((c) => DOCUMENTS_BY_CATEGORY[c].some((d) => d.value === value)) ?? null;
}

/** What to photograph, in plain words, for the chosen identity document. */
export function photographAdvice(value: string): string {
  switch (value) {
    case "passport":
      return "the page with your photo and the two lines of characters at the bottom, flat, in good light, the whole page in view";
    case "aadhaar":
      return "the front of the card, flat, in good light, all four corners in view (you may cover the first eight digits of the number)";
    default:
      return "the side with your photo, flat, in good light, all four corners in view and nothing covering it";
  }
}
